import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { FeaRangeFetcher } from "../../services/fea/feaFetcher";
import { memoryFeaFs, memoryFeaIndex, OpfsFeaStore, envelopeDirName } from "../../services/fea/opfsFeaStore";

const H = 16;
const S = 8;
const N_STEPS = 4;

/** A server blob: header bytes 0xAA, step i filled with byte i+1. */
function serverBlob(): Uint8Array {
  const b = new Uint8Array(H + S * N_STEPS).fill(0xaa, 0, H);
  for (let i = 0; i < N_STEPS; i++) b.fill(i + 1, H + i * S, H + (i + 1) * S);
  return b;
}

function fetcher(log: Array<[number, number]>, opts: { ranged?: boolean } = {}): FeaRangeFetcher {
  const blob = serverBlob();
  return async (_f, start, end) => {
    log.push([start, end]);
    if (opts.ranged === false) return { buf: blob.slice().buffer, ranged: false };
    return { buf: blob.slice(start, end + 1).buffer, ranged: true };
  };
}

function makeStore(extra: Partial<ConstructorParameters<typeof OpfsFeaStore>[0]> = {}) {
  let t = 1000;
  const fs = memoryFeaFs();
  const index = memoryFeaIndex();
  const store = new OpfsFeaStore({ fs, index, root: "/opfs/adapy-fea/v1", now: () => ++t, touchIntervalMs: 0, ...extra });
  return { fs, index, store };
}

const NEED = (steps: number[]) => ({ url: "fea.U.bin", header_bytes: H, stride_bytes: S, steps });

describe("OpfsFeaStore: sparse base strides", () => {
  it("fetches the header and only the strides asked for, once", async () => {
    const { store, fs } = makeStore();
    const log: Array<[number, number]> = [];
    assert.equal(await store.ensureStrides("src", 4, NEED([2, 0]), fetcher(log)), H + 2 * S);
    assert.deepEqual(log, [
      [0, H - 1],
      [H, H + S - 1],
      [H + 2 * S, H + 3 * S - 1],
    ]);
    assert.equal(await store.ensureStrides("src", 4, NEED([0, 2]), fetcher(log)), 0);
    assert.equal(log.length, 3);
    await store.ensureStrides("src", 4, NEED([3]), fetcher(log));
    assert.deepEqual(log[3], [H + 3 * S, H + 4 * S - 1]);
    const path = "/opfs/adapy-fea/v1/src/b4/base/fea.U.bin";
    assert.equal(await fs.size(path), H + 4 * S);
    // Present strides read back; the hole (step 1) does not.
    const s2 = await store.readFile("src", 4, "base/fea.U.bin", { start: H + 2 * S, end: H + 3 * S - 1 });
    assert.deepEqual([...s2!], Array(S).fill(3));
    assert.equal(await store.readFile("src", 4, "base/fea.U.bin", { start: H + S, end: H + 2 * S - 1 }), null);
    assert.equal(await store.readFile("src", 4, "base/fea.U.bin"), null);
    assert.ok(await store.readFile("src", 4, "base/fea.U.bin", { start: 0, end: H - 1 }));
  });

  it("keeps the whole object when the server ignores Range", async () => {
    const { store } = makeStore();
    const log: Array<[number, number]> = [];
    await store.ensureStrides("src", 4, NEED([1]), fetcher(log, { ranged: false }));
    assert.equal(log.length, 1);
    const all = await store.readFile("src", 4, "base/fea.U.bin");
    assert.deepEqual([...all!], [...serverBlob()]);
    assert.equal(await store.ensureStrides("src", 4, NEED([0, 1, 2, 3]), fetcher(log)), 0);
  });

  it("starts over when the file vanished under its index row", async () => {
    const { store, fs } = makeStore();
    const log: Array<[number, number]> = [];
    await store.ensureStrides("src", 4, NEED([0]), fetcher(log));
    fs.files.clear();
    await store.ensureStrides("src", 4, NEED([0]), fetcher(log));
    assert.equal(log.length, 4);
  });

  it("refuses a short range and paths outside the tree", async () => {
    const { store } = makeStore();
    const short: FeaRangeFetcher = async () => ({ buf: new ArrayBuffer(2), ranged: true });
    await assert.rejects(store.ensureStrides("src", 4, NEED([0]), short), /short range/);
    assert.equal(await store.readFile("src", 4, "../other/x"), null);
  });
});

describe("OpfsFeaStore: cases, envelopes, eviction", () => {
  async function seed(store: OpfsFeaStore, fs: ReturnType<typeof memoryFeaFs>) {
    await store.ensureStrides("a", 4, NEED([0, 1, 2, 3]), fetcher([]));
    for (const dir of ["101-aaaaaaaa", "102-bbbbbbbb"]) {
      const d = store.caseDir("a", 4, dir);
      await fs.writeFile(`${d}/fea.U.bin`, new Uint8Array(100));
      await fs.writeFile(`${d}/fea.case.json`, new Uint8Array(10));
      await store.recordCase("a", 4, dir, ["fea.U.bin", "fea.case.json"], { engine: "adacpp-wasm" });
    }
    const e = store.envelopeDir("a", 4, "sesam nodes/disp");
    await fs.writeFile(`${e}/fea.U.bin`, new Uint8Array(50));
    await fs.writeFile(`${e}/fea.envelope.json`, new Uint8Array(5));
    await store.recordEnvelope("a", 4, "sesam nodes/disp", ["fea.U.bin", "fea.envelope.json"]);
  }

  it("indexes sizes and serves case files", async () => {
    const { store, fs } = makeStore();
    await seed(store, fs);
    assert.equal(await store.hasCase("a", 4, "101-aaaaaaaa"), true);
    assert.equal(await store.hasCase("a", 4, "103-cccccccc"), false);
    assert.equal(await store.hasEnvelope("a", 4, "sesam nodes/disp"), true);
    assert.equal(envelopeDirName("sesam nodes/disp"), "sesam_nodes_disp");
    const u = await store.usage();
    assert.deepEqual(u.byKind, { base: H + 4 * S, case: 220, envelope: 55 });
    assert.equal(u.total, H + 4 * S + 275);
    assert.equal((await store.readFile("a", 4, "cases/101-aaaaaaaa/fea.U.bin"))!.byteLength, 100);
    const part = await store.readFile("a", 4, "cases/101-aaaaaaaa/fea.U.bin", { start: 10, end: 19 });
    assert.equal(part!.byteLength, 10);
  });

  it("evicts least recently used cases and envelopes before base blobs, never pinned", async () => {
    const { store, fs, index } = makeStore();
    await seed(store, fs);
    // Touch case 101: it becomes the most recent case.
    await store.readFile("a", 4, "cases/101-aaaaaaaa/fea.U.bin");
    let evicted = await store.evict({ targetBytes: H + 4 * S + 110 + 55 });
    assert.deepEqual(evicted, ["a/b4/case/102-bbbbbbbb"]);
    assert.equal(await fs.size(`${store.caseDir("a", 4, "102-bbbbbbbb")}/fea.U.bin`), -1);
    evicted = await store.evict({ targetBytes: H + 4 * S });
    assert.deepEqual(evicted.sort(), ["a/b4/case/101-aaaaaaaa", "a/b4/envelope/sesam_nodes_disp"]);
    // Base goes last -- unless pinned.
    await store.setPinned("a", true);
    assert.deepEqual(await store.evict({ targetBytes: 0 }), []);
    await store.setPinned("a", false);
    assert.deepEqual(await store.evict({ targetBytes: 0 }), ["a/b4/base/fea.U.bin"]);
    assert.equal(index.rows.size, 0);
  });

  it("keeps under half the free quota, and asks for persistence once", async () => {
    let persistCalls = 0;
    const { store, fs } = makeStore({
      estimate: async () => ({ quota: 1000, usage: 900 }),
      persist: async () => {
        persistCalls++;
        return true;
      },
    });
    await seed(store, fs);
    // held 323 + free 100/2 = 373 budget; reserving 200 more evicts until it fits.
    await store.reserve(200);
    const u = await store.usage();
    assert.ok(u.total + 200 <= 373, String(u.total));
    assert.equal(u.persisted, true);
    assert.equal(u.quota, 1000);
    await store.reserve(1);
    assert.equal(persistCalls, 1);
  });

  it("clears one source", async () => {
    const { store, fs, index } = makeStore();
    await seed(store, fs);
    await store.ensureStrides("b", 4, NEED([0]), fetcher([]));
    await store.clear("a");
    assert.deepEqual([...index.rows.values()].map((r) => r.sourceId), ["b"]);
    assert.ok([...fs.files.keys()].every((k) => k.includes("/b/")));
  });
});
