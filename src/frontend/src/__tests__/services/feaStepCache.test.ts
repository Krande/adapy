import assert from "node:assert/strict";
import { test } from "node:test";

import { StepCache } from "../../services/fea/stepCache";
import type { FeaManifest, FeaManifestField } from "../../services/viewerApi";
import { frameBytes, preloadFrames } from "../../utils/scene/fea/streaming/prefetch";
import { PaintedValues } from "../../utils/scene/fea/visibleValues";

const tick = () => new Promise((r) => setTimeout(r, 0));

test("the step cache evicts least-recently-used steps past its byte budget", async () => {
  const cache = new StepCache(32); // eight floats
  cache.set("a", Promise.resolve(new Float32Array(4)));
  cache.set("b", Promise.resolve(new Float32Array(4)));
  await tick();
  assert.equal(cache.bytes, 32);
  cache.get("a"); // a is now the most recent
  cache.set("c", Promise.resolve(new Float32Array(4)));
  await tick();
  assert.ok(cache.has("a") && cache.has("c"));
  assert.equal(cache.has("b"), false, "the least recently used goes first");
  assert.equal(cache.bytes, 32);
});

test("a step still in flight is never evicted, and a failed one never counts", async () => {
  const cache = new StepCache(16);
  let release!: (v: Float32Array) => void;
  cache.set("pending", new Promise((r) => (release = r)));
  cache.set("big", Promise.resolve(new Float32Array(8)));
  await tick();
  assert.ok(cache.has("pending"), "in flight: kept");
  cache.set("bad", Promise.reject(new Error("404")));
  await tick();
  release(new Float32Array(2));
  await tick();
  assert.ok(cache.bytes <= 16);
});

test("lowering the budget evicts at once", async () => {
  const cache = new StepCache(1024);
  for (const k of ["a", "b", "c"]) cache.set(k, Promise.resolve(new Float32Array(16)));
  await tick();
  cache.setBudget(64);
  assert.equal(cache.bytes, 64);
  assert.equal(cache.has("a"), false);
});

function transientManifest(): { manifest: FeaManifest; stress: FeaManifestField } {
  const steps = Array.from({ length: 10 }, (_, i) => ({ i, value: i, label: String(i) }));
  const disp = {
    name_canonical: "U", category: "displacement", support: "nodal", analysis_kind: "transient",
    components: ["U1", "U2", "U3"], n_steps: 10, steps,
    blob: { url: "fea.U.bin", header_bytes: 1024, stride_bytes: 120, dtype: "float32", byte_order: "little" },
  } as unknown as FeaManifestField;
  const stress = {
    name_canonical: "S", category: "stress", support: "gauss", analysis_kind: "static",
    components: ["S11"], n_steps: 10, steps,
    per_type: [
      { elem_type: "quad", n_elements: 20, n_ips: 1, blob: { url: "fea.S.quad.bin", header_bytes: 1024, stride_bytes: 80, dtype: "float32", byte_order: "little" } },
    ],
  } as unknown as FeaManifestField;
  return { manifest: { fields: [disp, stress] } as unknown as FeaManifest, stress };
}

test("a frame of stress costs its buckets plus the displacement that warps it", () => {
  const { manifest, stress } = transientManifest();
  assert.equal(frameBytes(manifest, stress, true), 80 + 120);
  assert.equal(frameBytes(manifest, stress, false), 80);
});

test("preload fetches as many frames as the budget holds, in play order", async () => {
  const { manifest, stress } = transientManifest();
  const requested: string[] = [];
  const rangeFetcher = async (filename: string, start: number, end: number) => {
    requested.push(`${filename}@${start}`);
    return { buf: new ArrayBuffer(end - start + 1), ranged: true };
  };
  const fetcher = async () => new ArrayBuffer(0);
  const progress: number[] = [];
  const result = await preloadFrames({
    manifest, field: stress, warpEnabled: true, fetcher, rangeFetcher, cacheKey: `t${Math.random()}`,
    nFrames: 10, startStep: 8,
    budgetBytes: Math.ceil((4 * 200) / 0.85), // four frames of 200 bytes
    onProgress: (p) => progress.push(p.loaded),
  });
  assert.deepEqual({ loaded: result.loaded, total: result.total, done: result.done }, { loaded: 4, total: 4, done: true });
  // Frames 8, 9, 0, 1: from the shown frame forward, wrapping.
  const stressSteps = requested.filter((r) => r.startsWith("fea.S")).map((r) => (Number(r.split("@")[1]) - 1024) / 80);
  assert.deepEqual([...stressSteps].sort((a, b) => a - b), [0, 1, 8, 9]);
  assert.equal(progress.at(-1), 4);
});

test("a stopped preload stops fetching and does not report done", async () => {
  const { manifest, stress } = transientManifest();
  const abort = new AbortController();
  let calls = 0;
  const rangeFetcher = async (_f: string, start: number, end: number) => {
    if (++calls === 2) abort.abort();
    return { buf: new ArrayBuffer(end - start + 1), ranged: true };
  };
  const result = await preloadFrames({
    manifest, field: stress, warpEnabled: false, fetcher: async () => new ArrayBuffer(0), rangeFetcher,
    cacheKey: `t${Math.random()}`, nFrames: 10, startStep: 0, budgetBytes: 1e9, signal: abort.signal, concurrency: 1,
  });
  assert.equal(result.done, false);
  assert.ok(result.loaded < 10);
});

test("painted values iterate like the Map they replace, skipping elements without one", () => {
  const painted = new PaintedValues();
  painted.add(["E1", "E2", "E3"], new Float64Array([1, NaN, 3]));
  painted.add(["E9"], new Float64Array([9]));
  assert.equal(painted.size, 3);
  assert.deepEqual([...painted], [["E1", 1], ["E3", 3], ["E9", 9]]);
  assert.deepEqual([...painted.values()], [1, 3, 9]);
});
