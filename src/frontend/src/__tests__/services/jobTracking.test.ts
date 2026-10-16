import assert from "node:assert/strict";
import { test } from "node:test";

// `viewerApi` reaches `services/auth/oidc.ts`, which reads `sessionStorage` at
// module load, so the browser globals are stubbed BEFORE the dynamic import below.
// The same shape the audit-range store test uses: a bare node process has no DOM,
// and this module's behaviour has nothing to do with one.
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

const { useConversionStore } = await import("@/state/conversionStore");
const { trackJob, rememberedJobs, resumeTrackedJobs, isJobTracked, registerJobFinisher } = await import(
  "@/services/jobTracking"
);
const { viewerApi } = await import("@/services/viewerApi");

// WHAT THIS IS FOR. A panel that enqueues a job and awaits its own poll loop is the
// only thing watching it, so closing the panel unmounts the watcher and the job
// disappears from the UI while running perfectly well server-side — no progress, no
// completion, and no way to tell a job that finished from one that died.
//
// `trackJob` hands the job to `useConversionStore`, which feeds the app-level toast
// and which core already repopulates from `my-jobs` on load. So a tracked job
// survives both closing the panel and a page reload.
//
// The poll itself is not exercised here: it needs a fetch and a clock, and the
// behaviour worth pinning is what lands in the store the instant a caller hands a
// job over — that is what makes the toast appear at all.
//
// EVERY TEST CLEARS UP AFTER ITSELF, and that is load-bearing rather than tidiness.
// `trackJob` starts a detached poll by design, and on a blip the poll keeps going —
// so an entry left in the store after the last assertion keeps a 1.5 s timer pending
// for the full attempt ceiling. Node stays alive while a timer is pending, so the
// whole suite hangs for 45 minutes instead of exiting. Clearing the entry makes the
// poll return on its next tick.

function clearAll(): void {
  for (const key of Object.keys(useConversionStore.getState().jobs)) {
    useConversionStore.getState().clearJob(key);
  }
}

/** Run a body with the store empty before and after. The `after` half is what
 *  stops the suite hanging; see the note above. */
function withCleanStore(body: () => void): () => void {
  return () => {
    clearAll();
    try {
      body();
    } finally {
      clearAll();
    }
  };
}

test("a tracked job appears in the store the toast renders from", withCleanStore(() => {
  const key = trackJob({ jobId: "job-1", scopeUrl: "shared", label: "Project tree" });

  const entry = useConversionStore.getState().jobs[key];
  assert.ok(entry, "nothing was put in the store, so no toast would appear");
  assert.equal(entry.jobId, "job-1");
  assert.equal(entry.sourceKey, "Project tree", "the label is what the toast shows");}));

test("it starts at queued, not running", withCleanStore(() => {
  // The worker may not have picked it up yet. Claiming `running` makes a job
  // waiting for a busy pool look stuck, and a single-seat pool is exactly where
  // jobs wait.
  const key = trackJob({ jobId: "job-2", scopeUrl: "shared", label: "x" });
  const entry = useConversionStore.getState().jobs[key];
  assert.equal(entry.status, "queued");
  assert.equal(entry.progress, 0);}));

test("two jobs with the same label do not overwrite each other", withCleanStore(() => {
  // The default key is the job id, which is always unique. Keying on the label
  // would make a second run of the same thing replace the first one's toast —
  // and the first job is still running.
  const a = trackJob({ jobId: "job-a", scopeUrl: "shared", label: "Project tree" });
  const b = trackJob({ jobId: "job-b", scopeUrl: "shared", label: "Project tree" });
  assert.notEqual(a, b);
  const jobs = useConversionStore.getState().jobs;
  assert.equal(Object.keys(jobs).length, 2);
  assert.equal(jobs[a].jobId, "job-a");
  assert.equal(jobs[b].jobId, "job-b");}));

test("a caller may name its own store key", withCleanStore(() => {
  // For a caller that wants one toast per logical operation rather than per job —
  // a two-job chain that should read as one piece of work.
  const key = trackJob({ jobId: "job-3", scopeUrl: "shared", label: "Export", storeKey: "cad:export" });
  assert.equal(key, "cad:export");
  assert.equal(useConversionStore.getState().jobs["cad:export"].jobId, "job-3");}));

test("a derived key is carried when the caller knows it, and empty when it does not", withCleanStore(() => {
  const withKey = trackJob({
    jobId: "job-4",
    scopeUrl: "shared",
    label: "x",
    derivedKey: "_derived/a.json",
  });
  assert.equal(useConversionStore.getState().jobs[withKey].derivedKey, "_derived/a.json");

  const without = trackJob({ jobId: "job-5", scopeUrl: "shared", label: "y" });
  assert.equal(
    useConversionStore.getState().jobs[without].derivedKey,
    "",
    "an unknown derived key must be empty, not undefined — the poll fills it in from the job",
  );}));

test("dismissing a tracked job removes it, so the poll has nothing to update", withCleanStore(() => {
  // The poll reads the entry back on every tick and returns when it is gone. That
  // is how a dismissed toast stops polling without a second cancellation channel.
  const key = trackJob({ jobId: "job-6", scopeUrl: "shared", label: "z" });
  useConversionStore.getState().clearJob(key);
  assert.equal(useConversionStore.getState().jobs[key], undefined);}));

// --- persisted entries (`persist`) and finishers ---------------------------------------------

const JOBS_KEY = "ada.jobs.toast";

test("a persisted job is remembered with its label and kind, and an unpersisted one is not", withCleanStore(() => {
  memory.delete(JOBS_KEY);
  trackJob({ jobId: "job-p", scopeUrl: "shared", label: "Rescan alpha", kind: "k", data: { a: 1 }, persist: true, storeKey: "r:a" });
  trackJob({ jobId: "job-q", scopeUrl: "shared", label: "not kept" });
  const kept = rememberedJobs();
  assert.deepEqual(Object.keys(kept), ["r:a"]);
  assert.equal(kept["r:a"].label, "Rescan alpha");
  assert.equal(kept["r:a"].kind, "k");
  assert.deepEqual(kept["r:a"].data, { a: 1 });
  assert.ok(isJobTracked("job-p") && !isJobTracked("job-zzz"));
  memory.delete(JOBS_KEY);
}));

test("a reload resumes the remembered entries in the toast; malformed ones are skipped", withCleanStore(() => {
  memory.set(
    JOBS_KEY,
    JSON.stringify({
      "r:b": { jobId: "job-r", scopeUrl: "shared", label: "Rescan beta", kind: "k" },
      broken: { scopeUrl: "shared" },
    }),
  );
  resumeTrackedJobs();
  const jobs = useConversionStore.getState().jobs;
  assert.equal(jobs["r:b"]?.sourceKey, "Rescan beta");
  assert.equal(jobs["r:b"]?.stage, "restoring");
  assert.equal(jobs.broken, undefined);
  memory.set(JOBS_KEY, "not json");
  assert.deepEqual(rememberedJobs(), {}, "unreadable storage is no entries, not an error");
  memory.delete(JOBS_KEY);
}));

test("a done job runs its finisher as the entry's last stage, then is forgotten", async () => {
  clearAll();
  memory.delete(JOBS_KEY);
  const api = viewerApi as unknown as { convertStatus: (id: string) => Promise<unknown> };
  const real = api.convertStatus;
  api.convertStatus = async () => ({ status: "done", progress: 1, stage: "ready", error: null, derived_key: "_d/x.json" });
  const stages: string[] = [];
  registerJobFinisher("test-finish", async ({ setStage, data, derivedKey }) => {
    setStage("waiting for the list", 0.95);
    stages.push(useConversionStore.getState().jobs["f:1"]?.stage ?? "");
    return { message: `finished ${(data as { n: number }).n} ${derivedKey}` };
  });
  try {
    trackJob({ jobId: "job-f", scopeUrl: "shared", label: "F", kind: "test-finish", data: { n: 3 }, persist: true, storeKey: "f:1" });
    for (let i = 0; i < 40 && useConversionStore.getState().jobs["f:1"]?.status !== "done"; i++) {
      await new Promise((r) => setTimeout(r, 100));
    }
    const entry = useConversionStore.getState().jobs["f:1"];
    assert.equal(entry?.status, "done");
    assert.equal(entry?.stage, "finished 3 _d/x.json");
    assert.deepEqual(stages, ["waiting for the list"]);
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(rememberedJobs(), {}, "a finished entry is not resumed again");
  } finally {
    api.convertStatus = real;
    clearAll();
  }
});
