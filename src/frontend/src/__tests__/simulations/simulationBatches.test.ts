// Batches of simulation runs: a batch's line (what it says, its tone, the order of the lines), which
// batches are open for the session, and the store against a server that keeps batches -- the runs
// of no batch listed apart, a batch's runs read a page at a time when it is opened, its cancelled
// runs behind a toggle, and a batch with runs going followed by its line, not run by run.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type {
  ListRunsOptions,
  SimulationBatch,
  SimulationRun,
  SimulationRunStatus,
} from "@/services/api/simulations";

const g = globalThis as Record<string, unknown>;
g.window ??= { location: new URL("http://localhost/"), addEventListener() {}, removeEventListener() {} };

const { createSimulationStore, BATCH_PAGE, BATCH_POLL_EVERY, mergeRuns } = await import("@/state/simulationStore");
const rows = await import("@/services/simulations/batchRows");

function run(id: string, status: SimulationRunStatus, over: Partial<SimulationRun> = {}): SimulationRun {
  return {
    run_id: id,
    provider: { id: "arm", domain: "robotics", engine: "fake", version: "1" },
    kind: null,
    parameters: { label: `frame ${id}` },
    inputs: [],
    started_by: { id: "me", display: null },
    created_at: "2026-11-21T10:00:00.000Z",
    started_at: null,
    finished_at: null,
    status,
    stage: status,
    progress: 0,
    error: null,
    job_id: `job-${id}`,
    artefacts: [],
    summary: {},
    cancel_requested: false,
    ...over,
  };
}

function batch(id: string, counts: Partial<Record<SimulationRunStatus, number>>, over: Partial<SimulationBatch> = {}): SimulationBatch {
  const full = { done: 0, running: 0, queued: 0, error: 0, cancelled: 0, ...counts };
  const count = Object.values(full).reduce((a, b) => a + b, 0);
  return {
    id,
    label: id,
    source: null,
    provider: { id: "arm", domain: "robotics" },
    providers: [{ id: "arm", domain: "robotics" }],
    count,
    counts: full,
    active: full.queued + full.running,
    progress: 0.5,
    newest_at: "2026-11-21T12:00:00.000Z",
    oldest_at: "2026-11-21T11:00:00.000Z",
    ...over,
  };
}

// ── the line ────────────────────────────────────────────────────────────────────────────────────

test("a batch's line counts its runs by status in a fixed order and leaves out what it has none of", () => {
  const b = batch("frame", { done: 86, running: 4, queued: 300, cancelled: 2 });
  assert.equal(rows.batchCountsLabel(b), "86 done · 4 running · 300 queued · 2 cancelled");
  assert.equal(rows.batchCountsLabel(b, { cancelled: false }), "86 done · 4 running · 300 queued");
  assert.equal(rows.batchCountsLabel(batch("x", { error: 1 })), "1 failed");
  assert.equal(rows.batchPercent(b), 50);
});

test("a batch's tone: going, then failed runs, then all cancelled, else done", () => {
  assert.equal(rows.batchTone(batch("a", { running: 1, done: 3 })), "running");
  assert.equal(rows.batchTone(batch("a", { queued: 2 })), "queued");
  assert.equal(rows.batchTone(batch("a", { done: 2, error: 1 })), "attention");
  assert.equal(rows.batchTone(batch("a", { cancelled: 3 })), "cancelled");
  assert.equal(rows.batchTone(batch("a", { done: 2, cancelled: 1 })), "done");
});

test("the list's lines are the batches and the runs of no batch, newest first", () => {
  const b = batch("frame", { done: 3 }, { newest_at: "2026-11-21T12:00:00.000Z" });
  const items = rows.runListItems(
    [
      run("new", "done", { created_at: "2026-11-21T13:00:00.000Z" }),
      run("old", "done", { created_at: "2026-11-21T09:00:00.000Z" }),
      // Named its batch, which the list has: left to the batch.
      run("in", "done", { created_at: "2026-11-21T14:00:00.000Z", batch: { id: "frame" } }),
    ],
    [b],
  );
  assert.deepEqual(items.map((i) => i.key), ["run:new", "batch:frame", "run:old"]);
  assert.equal(rows.listedRunCount([run("x", "done")], [b, batch("y", { queued: 2 })]), 6);
});

test("a batch is closed until opened, then stays as it was left", () => {
  rows.resetOpenBatches();
  assert.equal(rows.isBatchOpen("user:me", "frame"), false);
  rows.setBatchOpen("user:me", "frame", true);
  assert.equal(rows.isBatchOpen("user:me", "frame"), true);
  assert.equal(rows.isBatchOpen("shared", "frame"), false, "per scope");
  rows.setBatchOpen("user:me", "frame", false);
  assert.equal(rows.isBatchOpen("user:me", "frame"), false);
});

test("mergeRuns puts fresh runs in place and new ones first", () => {
  const merged = mergeRuns([run("a", "running"), run("b", "done")], [run("a", "done"), run("c", "queued")]);
  assert.deepEqual(merged.map((r) => `${r.run_id}:${r.status}`), ["c:queued", "a:done", "b:done"]);
});

// ── the store ───────────────────────────────────────────────────────────────────────────────────

/** A server with batches: `batched` holds each batch's runs, newest first. */
class BatchApi {
  calls: string[] = [];
  loose: SimulationRun[] = [];
  batched = new Map<string, SimulationRun[]>();
  lines: SimulationBatch[] = [];
  supported = true;

  async listProviders() {
    return { domains: [], providers: [] };
  }
  async listBatches(scope: string) {
    this.calls.push(`listBatches ${scope}`);
    return this.supported ? { batches: [...this.lines], unbatched: this.loose.length } : null;
  }
  async listRuns(scope: string, opts: ListRunsOptions = {}) {
    this.calls.push(`listRuns ${scope}${opts.batch ? ` batch=${opts.batch}` : ""}`);
    return [...this.loose];
  }
  async listRunsPage(scope: string, opts: ListRunsOptions = {}) {
    const status = opts.status?.join(",") ?? "";
    this.calls.push(`page ${opts.batch} limit=${opts.limit}${status ? ` status=${status}` : ""}${opts.before ? ` before=${opts.before}` : ""}`);
    let all = this.batched.get(opts.batch ?? "") ?? [];
    if (opts.status) all = all.filter((r) => opts.status!.includes(r.status));
    if (opts.before) all = all.filter((r) => r.run_id < opts.before!);
    const runs = all.slice(0, opts.limit ?? 50);
    return { runs, next: all.length > runs.length ? runs[runs.length - 1].run_id : null };
  }
  async getRun(_scope: string, runId: string) {
    this.calls.push(`getRun ${runId}`);
    return run(runId, "done");
  }
  async startRun(_scope: string, body: { batch?: { id: string } }) {
    return run("zz-new", "queued", { batch: body.batch ?? null });
  }
  async cancelRun(_scope: string, runId: string) {
    const r = [...this.batched.values()].flat().find((x) => x.run_id === runId) ?? run(runId, "running");
    return { run: { ...r, cancel_requested: true }, cancelled: true };
  }
}

class FakeTimer {
  pending: Array<{ fn: () => void; cancelled: boolean }> = [];
  schedule = (fn: () => void) => {
    const entry = { fn, cancelled: false };
    this.pending.push(entry);
    return () => {
      entry.cancelled = true;
    };
  };
  get armed() {
    return this.pending.filter((p) => !p.cancelled).length;
  }
  async fire() {
    const entry = this.pending.find((p) => !p.cancelled);
    assert.ok(entry, "no timer armed");
    this.pending = this.pending.filter((p) => p !== entry);
    entry.fn();
    await new Promise((r) => setTimeout(r, 0));
  }
}

let api: BatchApi;
let timer: FakeTimer;
let store: ReturnType<typeof createSimulationStore>;
const s = () => store.getState();

/** `n` runs of a batch, newest first: ids that sort as the server sorts them. */
const many = (prefix: string, n: number, status: (i: number) => SimulationRunStatus) =>
  Array.from({ length: n }, (_, i) => run(`${prefix}${String(n - i).padStart(4, "0")}`, status(i), { batch: { id: prefix } }));

beforeEach(() => {
  api = new BatchApi();
  timer = new FakeTimer();
  store = createSimulationStore(api, timer.schedule);
});

test("a server with batches: the batches are lines, and only the runs of no batch are listed", async () => {
  api.loose = [run("solo", "done")];
  api.lines = [batch("frame", { done: 3 })];
  await s().loadRuns("user:me");
  assert.equal(s().batchesSupported, true);
  assert.deepEqual(s().batches.map((b) => b.id), ["frame"]);
  assert.deepEqual(api.calls, ["listBatches user:me", "listRuns user:me batch=none"]);
  assert.equal(timer.armed, 0, "nothing going: nothing followed");
});

test("a server without batches lists every run, as before", async () => {
  api.supported = false;
  api.loose = [run("a", "done")];
  await s().loadRuns("user:me");
  assert.equal(s().batchesSupported, false);
  assert.deepEqual(api.calls, ["listBatches user:me", "listRuns user:me"]);
});

test("a batch's runs are read a page at a time when it is opened, the cancelled ones left out", async () => {
  const runs = many("frame", 120, (i) => (i % 4 === 3 ? "cancelled" : "done"));
  api.batched.set("frame", runs);
  api.lines = [batch("frame", { done: 90, cancelled: 30 })];
  await s().loadRuns("user:me");
  await s().loadBatchRuns("user:me", "frame");
  const first = s().batchRuns.frame;
  assert.equal(first.runs.length, BATCH_PAGE);
  assert.ok(first.runs.every((r) => r.status !== "cancelled"));
  assert.ok(first.next);
  await s().loadBatchRuns("user:me", "frame", { more: true });
  assert.equal(s().batchRuns.frame.runs.length, 90, "the rest of the runs that are not cancelled");
  assert.equal(s().batchRuns.frame.next, null);
  assert.match(api.calls.at(-1)!, /before=/);

  // Shown with the cancelled ones: read again from the start.
  await s().loadBatchRuns("user:me", "frame", { cancelled: true });
  assert.equal(s().batchRuns.frame.cancelled, true);
  assert.equal(s().batchRuns.frame.runs.length, BATCH_PAGE);
  assert.ok(s().batchRuns.frame.runs.some((r) => r.status === "cancelled"));
  assert.doesNotMatch(api.calls.at(-1)!, /status=/);
});

test("a batch with runs going is followed by its line every few rounds, not run by run", async () => {
  const runs = many("frame", 300, (i) => (i < 10 ? "running" : "queued"));
  api.batched.set("frame", runs);
  api.lines = [batch("frame", { running: 10, queued: 290 })];
  await s().loadRuns("user:me");
  await s().loadBatchRuns("user:me", "frame");
  assert.equal(timer.armed, 1);
  api.calls = [];
  for (let i = 0; i < BATCH_POLL_EVERY; i += 1) await timer.fire();
  assert.equal(api.calls.filter((c) => c.startsWith("getRun")).length, 0, "no batch run is asked for on its own");
  assert.deepEqual(
    api.calls.filter((c) => c.startsWith("listBatches") || c.startsWith("page")),
    ["listBatches user:me", `page frame limit=${BATCH_PAGE} status=queued,running,done,error`],
    "one request for the lines and one for the open page",
  );

  // The batch ends: its line says so, and the following stops.
  api.lines = [batch("frame", { done: 300 })];
  api.batched.set("frame", runs.map((r) => ({ ...r, status: "done" as const })));
  for (let i = 0; i < BATCH_POLL_EVERY; i += 1) await timer.fire();
  assert.equal(s().batches[0].active, 0);
  assert.ok(s().batchRuns.frame.runs.every((r) => r.status === "done"));
  assert.equal(timer.armed, 0);
});

test("a run started in a batch is not listed alone: the batches are asked for on the next round", async () => {
  api.lines = [];
  await s().loadRuns("user:me");
  const started = await s().startRun("user:me", { provider: "arm", batch: { id: "frame", label: "Frame" } });
  assert.equal(started?.batch?.id, "frame");
  assert.deepEqual(s().runs, []);
  assert.equal(timer.armed, 1);
  api.lines = [batch("frame", { queued: 1 })];
  await timer.fire();
  assert.deepEqual(s().batches.map((b) => b.id), ["frame"]);
});

test("cancelling a run of an open batch updates it there", async () => {
  api.batched.set("frame", [run("r1", "running", { batch: { id: "frame" } })]);
  api.lines = [batch("frame", { running: 1 })];
  await s().loadRuns("user:me");
  await s().loadBatchRuns("user:me", "frame");
  await s().cancelRun("user:me", "r1");
  assert.equal(s().batchRuns.frame.runs[0].cancel_requested, true);
  assert.deepEqual(s().runs, []);
});
