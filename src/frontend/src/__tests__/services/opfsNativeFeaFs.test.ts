import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  opfsNativeFeaFs,
  type OpfsDirHandleLike,
  type OpfsFileHandleLike,
  type OpfsSyncHandle,
} from "../../services/fea/opfsFeaStore";

/** A small OPFS stand-in: directories, files, and EXCLUSIVE sync access handles. */
class FakeFile implements OpfsFileHandleLike {
  data = new Uint8Array(0);
  locked = false;
  opens = 0;
  async createSyncAccessHandle(): Promise<OpfsSyncHandle> {
    if (this.locked) {
      const err = new Error("locked");
      err.name = "NoModificationAllowedError";
      throw err;
    }
    this.locked = true;
    this.opens++;
    const file = this;
    let open = true;
    const live = () => {
      if (!open) throw new Error("handle closed");
    };
    return {
      read(buf, opts) {
        live();
        const at = opts?.at ?? 0;
        const n = Math.max(0, Math.min(buf.byteLength, file.data.byteLength - at));
        buf.set(file.data.subarray(at, at + n));
        return n;
      },
      write(buf, opts) {
        live();
        const at = opts?.at ?? 0;
        if (at + buf.byteLength > file.data.byteLength) {
          const next = new Uint8Array(at + buf.byteLength);
          next.set(file.data);
          file.data = next;
        }
        file.data.set(buf, at);
        return buf.byteLength;
      },
      truncate(size) {
        live();
        file.data = file.data.slice(0, size);
      },
      getSize: () => (live(), file.data.byteLength),
      flush: () => live(),
      close() {
        open = false;
        file.locked = false;
      },
    };
  }
}

class FakeDir implements OpfsDirHandleLike {
  entries = new Map<string, FakeDir | FakeFile>();
  async getDirectoryHandle(name: string, opts?: { create?: boolean }): Promise<FakeDir> {
    let e = this.entries.get(name);
    if (!e && opts?.create) this.entries.set(name, (e = new FakeDir()));
    if (!(e instanceof FakeDir)) throw Object.assign(new Error(name), { name: "NotFoundError" });
    return e;
  }
  async getFileHandle(name: string, opts?: { create?: boolean }): Promise<FakeFile> {
    let e = this.entries.get(name);
    if (!e && opts?.create) this.entries.set(name, (e = new FakeFile()));
    if (!(e instanceof FakeFile)) throw Object.assign(new Error(name), { name: "NotFoundError" });
    return e;
  }
  async removeEntry(name: string): Promise<void> {
    const e = this.entries.get(name);
    if (!e) throw Object.assign(new Error(name), { name: "NotFoundError" });
    const locked = (x: FakeDir | FakeFile): boolean =>
      x instanceof FakeFile ? x.locked : [...x.entries.values()].some(locked);
    if (locked(e)) throw Object.assign(new Error(name), { name: "NoModificationAllowedError" });
    this.entries.delete(name);
  }
}

describe("opfsNativeFeaFs", () => {
  it("writes sparse ranges, reads them back, and removes trees (closing its handles first)", async () => {
    const root = new FakeDir();
    const fs = opfsNativeFeaFs(root, { idleMs: 60_000 });
    await fs.mkdirTree("/adapy-fea/v1/src/b4/base");
    assert.equal(await fs.size("/adapy-fea/v1/src/b4/base/fea.U.bin"), -1);
    await fs.writeAt("/adapy-fea/v1/src/b4/base/fea.U.bin", 8, new Uint8Array([1, 2, 3, 4]));
    await fs.writeAt("/adapy-fea/v1/src/b4/base/fea.U.bin", 0, new Uint8Array([9]));
    assert.equal(await fs.size("/adapy-fea/v1/src/b4/base/fea.U.bin"), 12);
    assert.deepEqual([...(await fs.read("/adapy-fea/v1/src/b4/base/fea.U.bin", 7, 3))], [0, 1, 2]);
    await fs.writeFile("/adapy-fea/v1/src/b4/cases/1-x/fea.case.json", new Uint8Array([5, 5]));
    await fs.writeFile("/adapy-fea/v1/src/b4/cases/1-x/fea.case.json", new Uint8Array([6]));
    assert.deepEqual([...(await fs.read("/adapy-fea/v1/src/b4/cases/1-x/fea.case.json", 0, 1))], [6]);
    // A burst reuses one handle per file.
    const base = (await (await (await (await (await root.getDirectoryHandle("adapy-fea")).getDirectoryHandle("v1")).getDirectoryHandle("src")).getDirectoryHandle("b4")).getDirectoryHandle("base")) as FakeDir;
    assert.equal((base.entries.get("fea.U.bin") as FakeFile).opens, 1);
    // Removal closes the open handles under the path, then removes.
    await fs.remove("/adapy-fea/v1/src");
    assert.equal(await fs.size("/adapy-fea/v1/src/b4/base/fea.U.bin"), -1);
    await fs.remove("/adapy-fea/v1/nothing-here");
    await fs.closeAll();
  });

  it("closes everything when idle, so another context can open the files", async () => {
    const root = new FakeDir();
    const fs = opfsNativeFeaFs(root, { idleMs: 5 });
    await fs.writeFile("/a/f.bin", new Uint8Array([1]));
    const f = (await (await root.getDirectoryHandle("a")).getFileHandle("f.bin")) as FakeFile;
    assert.equal(f.locked, true);
    await new Promise((r) => setTimeout(r, 30));
    assert.equal(f.locked, false);
  });

  it("waits for a handle another context holds, then gives up", async () => {
    const root = new FakeDir();
    const other = await (await root.getDirectoryHandle("a", { create: true })).getFileHandle("f.bin", { create: true });
    const held = await other.createSyncAccessHandle();
    const sleeps: number[] = [];
    const fs = opfsNativeFeaFs(root, {
      idleMs: 0,
      retries: 2,
      sleep: async (ms) => {
        sleeps.push(ms);
        if (sleeps.length === 2) held.close();
      },
    });
    await fs.writeFile("/a/f.bin", new Uint8Array([7]));
    assert.deepEqual(sleeps, [50, 100]);
    const held2 = await other.createSyncAccessHandle();
    const fs2 = opfsNativeFeaFs(root, { idleMs: 0, retries: 1, sleep: async () => {} });
    await assert.rejects(fs2.writeFile("/a/f.bin", new Uint8Array([8])), /locked/);
    held2.close();
  });

  it("opens at most maxOpen handles", async () => {
    const root = new FakeDir();
    const fs = opfsNativeFeaFs(root, { idleMs: 60_000, maxOpen: 2 });
    for (const n of ["a", "b", "c"]) await fs.writeFile(`/d/${n}.bin`, new Uint8Array([1]));
    const d = (await root.getDirectoryHandle("d")) as FakeDir;
    assert.deepEqual(["a", "b", "c"].map((n) => (d.entries.get(`${n}.bin`) as FakeFile).locked), [false, true, true]);
    await fs.closeAll();
  });
});
