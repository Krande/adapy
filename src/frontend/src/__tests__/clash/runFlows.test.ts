// `runClashCheckFlow` / `runClashDetailFlow` are the pure orchestration behind the store's
// `runCheck` / `runDetail` actions -- driven here against fakes (no network, no timers), mirroring
// `__tests__/assets/delivery.test.ts`'s split for `assets/delivery.ts`'s `loadNode`. Pins:
//
//   - `cached: true` reads the result straight from `derived_key` WITHOUT enqueueing: `jobStatus`
//     is never called (Decision 3's "a repeat is not a job", the same discipline
//     `assetsApi.buildAssetNode`'s `cached` already follows) -- exercised for both the check and
//     the detail hand-off.
//   - `cached: false` polls `jobStatus` to `done`, hands the job to `trackJob` for the toast, and
//     only then reads the result.
//   - a job that ends `error`/`cancelled` is refused with the reason named.
//   - `cached: false` with no `job_id` is refused rather than silently doing nothing.
//
// See `parseClashResult.test.ts` for why the browser globals are stubbed before the dynamic
// import.

import assert from "node:assert/strict";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { runClashCheckFlow, runClashDetailFlow, ClashResultError } = await import("@/state/clashCheckStore");

const SCOPE = "user:me";

function resultDoc(): Record<string, unknown> {
  return {
    schema: "ada.clash/result@1",
    source_key: "models/plant-a.ifc",
    options: {},
    counts: { members: 2, joints: 1 },
    joints: [
      {
        id: "j1",
        centre: [0, 0, 0],
        members: [{ name: "bm1", kind: "BEAM" }, { name: "bm2", kind: "BEAM" }],
        type_key: "k",
        type_label: "k",
        applicable: [],
      },
    ],
    groups: [{ type_key: "k", type_label: "k", count: 1, joint_ids: ["j1"], applicable: [] }],
    provenance: {},
    warnings: [],
  };
}

function detailItem(name: string) {
  return { name, slug: "gusset", type: "Gusset", members: ["g1", "g2"], plates: 1, welds: 2, centre: [0, 0, 3] };
}

function noWait(_ms: number): Promise<void> {
  return Promise.resolve();
}

function fakeApi(over: Record<string, unknown> = {}) {
  const calls = {
    runClashCheck: 0,
    runClashDetail: 0,
    getClashResult: 0,
    getDetailStats: 0,
    jobStatus: [] as string[],
  };
  const api = {
    async runClashCheck(_scope: string, _body: unknown) {
      calls.runClashCheck++;
      throw new Error("runClashCheck not stubbed");
    },
    async runClashDetail(_scope: string, _body: unknown) {
      calls.runClashDetail++;
      throw new Error("runClashDetail not stubbed");
    },
    async getClashResult(_scope: string, _key: string) {
      calls.getClashResult++;
      return resultDoc();
    },
    async getDetailStats(_scope: string, _key: string) {
      calls.getDetailStats++;
      return { joints: { count: 1, by_type: [{ slug: "gusset", name: "Gusset", count: 1 }], items: [detailItem("g0")] } };
    },
    async jobStatus(jobId: string) {
      calls.jobStatus.push(jobId);
      throw new Error("jobStatus not stubbed");
    },
    ...over,
  };
  return { api, calls };
}

test("runClashCheckFlow: cached:true reads the result and never polls a job", async () => {
  const { api, calls } = fakeApi({
    async runClashCheck() {
      return { job_id: null, derived_key: "_derived/clash/abc/result.json", cached: true };
    },
  });
  const tracked: unknown[] = [];
  const { result, derivedKey, cached } = await runClashCheckFlow(
    { api, trackJob: (o) => tracked.push(o), wait: noWait },
    SCOPE,
    "models/plant-a.ifc",
    {},
  );
  assert.equal(cached, true);
  assert.equal(derivedKey, "_derived/clash/abc/result.json");
  assert.equal(result.joints.length, 1);
  assert.equal(calls.jobStatus.length, 0, "a cached run must not poll a job");
  assert.equal(tracked.length, 0, "a cached run must not enqueue anything for the toast either");
});

test("runClashCheckFlow: cached:false polls to done, tracks the job, then reads the result", async () => {
  let tick = 0;
  const { api, calls } = fakeApi({
    async runClashCheck() {
      return { job_id: "job-1", derived_key: "_derived/clash/abc/result.json", cached: false };
    },
    async jobStatus(jobId: string) {
      calls.jobStatus.push(jobId);
      tick++;
      return tick < 2 ? { status: "running", error: null } : { status: "done", error: null };
    },
  });
  const tracked: { jobId: string; label: string }[] = [];
  const { cached } = await runClashCheckFlow({ api, trackJob: (o) => tracked.push(o), wait: noWait }, SCOPE, "models/plant-a.ifc", {});
  assert.equal(cached, false);
  assert.equal(calls.jobStatus.length, 2);
  assert.equal(tracked.length, 1);
  assert.equal(tracked[0].jobId, "job-1");
});

test("runClashCheckFlow: an errored job is refused with the reason named", async () => {
  const { api } = fakeApi({
    async runClashCheck() {
      return { job_id: "job-2", derived_key: "k", cached: false };
    },
    async jobStatus() {
      return { status: "error", error: "the beam-to-beam pass failed" };
    },
  });
  await assert.rejects(
    runClashCheckFlow({ api, wait: noWait }, SCOPE, "models/plant-a.ifc", {}),
    (err: unknown) => err instanceof ClashResultError && /the beam-to-beam pass failed/.test((err as Error).message),
  );
});

test("runClashCheckFlow: cached:false with no job_id is refused, not silently ignored", async () => {
  const { api } = fakeApi({
    async runClashCheck() {
      return { job_id: null, derived_key: "k", cached: false };
    },
  });
  await assert.rejects(runClashCheckFlow({ api, wait: noWait }, SCOPE, "models/plant-a.ifc", {}), ClashResultError);
});

test("runClashDetailFlow: cached:true (or absent) reads the derived key without polling", async () => {
  const { api, calls } = fakeApi({
    async runClashDetail() {
      return { job_id: null, derived_key: "_derived/clash/detail/overlay.glb", cached: true };
    },
  });
  const { derivedKey, cached } = await runClashDetailFlow({ api, wait: noWait }, SCOPE, "_derived/clash/abc/result.json", ["j1"], "builtin.girder_gusset");
  assert.equal(cached, true);
  assert.equal(derivedKey, "_derived/clash/detail/overlay.glb");
  assert.equal(calls.jobStatus.length, 0);
});

test("runClashDetailFlow: cached:false polls to done and tracks the job", async () => {
  const { api, calls } = fakeApi({
    async runClashDetail() {
      return { job_id: "job-3", derived_key: "_derived/clash/detail/overlay.glb", cached: false };
    },
    async jobStatus(jobId: string) {
      calls.jobStatus.push(jobId);
      return { status: "done", error: null };
    },
  });
  const tracked: unknown[] = [];
  const { cached } = await runClashDetailFlow({ api, trackJob: (o) => tracked.push(o), wait: noWait }, SCOPE, "k", ["j1"], "builtin.girder_gusset");
  assert.equal(cached, false);
  assert.equal(calls.jobStatus.length, 1);
  assert.equal(tracked.length, 1);
});
