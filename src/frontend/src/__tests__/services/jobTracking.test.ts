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
const { trackJob } = await import("@/services/jobTracking");

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

function reset(): void {
  for (const key of Object.keys(useConversionStore.getState().jobs)) {
    useConversionStore.getState().clearJob(key);
  }
}

test("a tracked job appears in the store the toast renders from", () => {
  reset();
  const key = trackJob({ jobId: "job-1", scopeUrl: "shared", label: "Project tree" });

  const entry = useConversionStore.getState().jobs[key];
  assert.ok(entry, "nothing was put in the store, so no toast would appear");
  assert.equal(entry.jobId, "job-1");
  assert.equal(entry.sourceKey, "Project tree", "the label is what the toast shows");
});

test("it starts at queued, not running", () => {
  // The worker may not have picked it up yet. Claiming `running` makes a job
  // waiting for a busy pool look stuck, and a single-seat pool is exactly where
  // jobs wait.
  reset();
  const key = trackJob({ jobId: "job-2", scopeUrl: "shared", label: "x" });
  const entry = useConversionStore.getState().jobs[key];
  assert.equal(entry.status, "queued");
  assert.equal(entry.progress, 0);
});

test("two jobs with the same label do not overwrite each other", () => {
  // The default key is the job id, which is always unique. Keying on the label
  // would make a second run of the same thing replace the first one's toast —
  // and the first job is still running.
  reset();
  const a = trackJob({ jobId: "job-a", scopeUrl: "shared", label: "Project tree" });
  const b = trackJob({ jobId: "job-b", scopeUrl: "shared", label: "Project tree" });
  assert.notEqual(a, b);
  const jobs = useConversionStore.getState().jobs;
  assert.equal(Object.keys(jobs).length, 2);
  assert.equal(jobs[a].jobId, "job-a");
  assert.equal(jobs[b].jobId, "job-b");
});

test("a caller may name its own store key", () => {
  // For a caller that wants one toast per logical operation rather than per job —
  // a two-job chain that should read as one piece of work.
  reset();
  const key = trackJob({ jobId: "job-3", scopeUrl: "shared", label: "Export", storeKey: "e3d:export" });
  assert.equal(key, "e3d:export");
  assert.equal(useConversionStore.getState().jobs["e3d:export"].jobId, "job-3");
});

test("a derived key is carried when the caller knows it, and empty when it does not", () => {
  reset();
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
  );
});

test("dismissing a tracked job removes it, so the poll has nothing to update", () => {
  // The poll reads the entry back on every tick and returns when it is gone. That
  // is how a dismissed toast stops polling without a second cancellation channel.
  reset();
  const key = trackJob({ jobId: "job-6", scopeUrl: "shared", label: "z" });
  useConversionStore.getState().clearJob(key);
  assert.equal(useConversionStore.getState().jobs[key], undefined);
});
