// The native converters' OPFS gate and input writes: an old module's `mountOpfs` is never trusted (it
// reported success and then trapped on every file operation), and an input is always written fresh
// (WASMFS 4.0.9's writeFile appended to an existing file).

import assert from "node:assert/strict";
import { test } from "node:test";

import { ensureOpfsMounted, writeFileFresh, type WasmfsModule } from "../../utils/nativeConvert/opfsWasmfs";

function fakeFs() {
  const files = new Map<string, Uint8Array>();
  const calls: string[] = [];
  return {
    files,
    calls,
    FS: {
      readFile: (p: string) => files.get(p) ?? new Uint8Array(),
      unlink: (p: string) => {
        calls.push(`unlink ${p}`);
        if (!files.delete(p)) throw new Error("ENOENT");
      },
      mkdir: () => undefined,
      open: () => ({}),
      write: () => 0,
      close: () => undefined,
      // The 4.0.9 bug: appends to whatever is there.
      writeFile: (p: string, data: Uint8Array) => {
        calls.push(`write ${p}`);
        const prev = files.get(p) ?? new Uint8Array();
        const out = new Uint8Array(prev.length + data.length);
        out.set(prev);
        out.set(data, prev.length);
        files.set(p, out);
      },
    },
  };
}

test("an input is written fresh even where writeFile appends", () => {
  const m = fakeFs();
  writeFileFresh(m, "/in.stp", new Uint8Array([1, 2, 3, 4]));
  writeFileFresh(m, "/in.stp", new Uint8Array([9]));
  assert.deepEqual([...m.files.get("/in.stp")!], [9], "the second input replaced the first");
  assert.deepEqual(m.calls.slice(-2), ["unlink /in.stp", "write /in.stp"]);
});

test("a module with only the old mountOpfs is never mounted; outside a worker nothing is", async () => {
  const old = { ...fakeFs(), mountOpfs: () => 0 } as unknown as WasmfsModule;
  assert.equal(await ensureOpfsMounted(old), false);
  let asked = false;
  const fresh = {
    ...fakeFs(),
    opfsMount: async () => {
      asked = true;
    },
  } as unknown as WasmfsModule;
  // node is not a dedicated worker: the gate refuses before asking the module.
  assert.equal(await ensureOpfsMounted(fresh), false);
  assert.equal(asked, false);
});
