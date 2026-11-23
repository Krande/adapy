// Managing runs after they were started: picking many in the list (toggle, shift-click range, all in
// view), what the confirmations and results say, a new batch's id from its label, and the store's
// move / remove / delete -- after which the batch lines and the open pages are the server's again.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type {
  DeleteBatchResponse,
  ListRunsOptions,
  MoveRunsResponse,
  RunAdminResponse,
  SimulationBatch,
  SimulationRun,
  SimulationRunStatus,
} from "@/services/api/simulations";

const g = globalThis as Record<string, unknown>;
g.window ??= { location: new URL("http://localhost/"), addEventListener() {}, removeEventListener() {} };

const { createSimulationStore } = await import("@/state/simulationStore");
const sel = await import("@/services/simulations/runSelection");

// ── selection ───────────────────────────────────────────────────────────────────────────────────

const ORDER = ["a", "b", "c", "d", "e"];
const ids = (s: { ids: ReadonlySet<string> }) => [...s.ids].sort();

test("a click toggles a run and anchors a range; a shift-click selects from the anchor", () => {
  let s = sel.clickRun(sel.EMPTY_SELECTION, ORDER, "b", false);
  assert.deepEqual(ids(s), ["b"]);
  s = sel.clickRun(s, ORDER, "d", true);
  assert.deepEqual(ids(s), ["b", "c", "d"]);
  // Upwards too, and the anchor stays where it was.
  s = sel.clickRun(s, ORDER, "a", true);
  assert.deepEqual(ids(s), ["a", "b", "c", "d"]);
  assert.equal(s.anchor, "b");
  s = sel.clickRun(s, ORDER, "c", false);
  assert.deepEqual(ids(s), ["a", "b", "d"]);
  assert.equal(s.anchor, "c");
  // An anchor no longer in view: a shift-click is a plain toggle.
  s = sel.clickRun({ ids: new Set(), anchor: "gone" }, ORDER, "e", true);
  assert.deepEqual(ids(s), ["e"]);
});

test("select all in view adds every run shown; again, takes them out", () => {
  let s = sel.toggleRun(sel.EMPTY_SELECTION, "elsewhere");
  s = sel.selectAllInView(s, ORDER);
  assert.deepEqual(ids(s), ["a", "b", "c", "d", "e", "elsewhere"]);
  s = sel.selectAllInView(s, ORDER);
  assert.deepEqual(ids(s), ["elsewhere"]);
  assert.deepEqual(ids(sel.pruneSelection(sel.selectAllInView(s, ORDER), (id) => id !== "c")), ["a", "b", "d", "e", "elsewhere"]);
});

test("the confirmation counts runs and says their size when it is known", () => {
  assert.equal(sel.deleteRunsTitle(37, 12 * 1024 ** 3), "Delete 37 runs (12 GB)?");
  assert.equal(sel.deleteRunsTitle(1, 0), "Delete 1 run?");
  assert.equal(sel.deleteRunsTitle(2, null), "Delete 2 runs?");
  assert.equal(sel.bytesLabel(1536), "1.5 kB");
  assert.equal(sel.bytesLabel(340 * 1024 ** 2), "340 MB");
  assert.match(sel.deleteRunsBody(3, 2).join(" "), /2 runs are still going/);
  assert.equal(sel.deleteBatchTitle("Frame A", 12), 'Delete batch "Frame A" (12 runs)?');
});

test("a result says what was done and why the rest was not", () => {
  const res = {
    results: [
      { run_id: "a", status: "deleted", bytes: 10 },
      { run_id: "b", status: "deleted", already: true },
      { run_id: "c", status: "failed", code: "forbidden", reason: "not yours" },
      { run_id: "d", status: "failed", code: "forbidden", reason: "not yours" },
    ],
  };
  assert.equal(sel.resultSummary(res, "deleted"), "Deleted 1 · 1 already gone · 2 failed: not yours (2)");
  assert.equal(sel.hasFailures(res), true);
  assert.equal(
    sel.resultSummary({ results: [{ run_id: "a", status: "moved" }, { run_id: "b", status: "unchanged" }] }, "moved"),
    "Moved 1 · 1 unchanged",
  );
});

test("a new batch's id is its label, made safe, with a stamp", () => {
  const id = sel.batchIdForLabel("Frame A / welds (v2)", new Date(Date.UTC(2026, 10, 23, 9, 5)), () => 0.5);
  assert.match(id, /^frame-a-welds-v2-20261123T0905-[0-9a-z]{4}$/);
  assert.match(sel.batchIdForLabel("✓✓", new Date(), () => 0), /^batch-\d{8}T\d{4}-0000$/);
});

// ── the store ───────────────────────────────────────────────────────────────────────────────────

function run(id: string, status: SimulationRunStatus, batch: string | null = null): SimulationRun {
  return {
    run_id: id,
    provider: { id: "arm", domain: "robotics", engine: "fake", version: "1" },
    kind: null,
    parameters: {},
    inputs: [],
    started_by: { id: "me", display: null },
    created_at: "2026-11-21T10:00:00.000Z",
    started_at: null,
    finished_at: null,
    status,
    stage: status,
    progress: 0,
    error: null,
    job_id: null,
    artefacts: [],
    summary: {},
    cancel_requested: false,
    batch: batch ? { id: batch } : null,
  };
}

/** A server that keeps runs and their batches, and answers the management routes over them. */
class AdminApi {
  runs = new Map<string, SimulationRun>();
  calls: string[] = [];

  private lines(): SimulationBatch[] {
    const by = new Map<string, SimulationRun[]>();
    for (const r of this.runs.values()) if (r.batch) by.set(r.batch.id, [...(by.get(r.batch.id) ?? []), r]);
    return [...by.entries()].map(([id, rs]) => {
      const counts = { done: 0, running: 0, queued: 0, error: 0, cancelled: 0 };
      for (const r of rs) counts[r.status] += 1;
      return {
        id,
        label: id,
        source: null,
        provider: { id: "arm", domain: "robotics" },
        providers: [],
        count: rs.length,
        counts,
        active: counts.queued + counts.running,
        progress: 1,
        newest_at: null,
        oldest_at: null,
      };
    });
  }
  async listProviders() {
    return { domains: [], providers: [] };
  }
  async listBatches() {
    return { batches: this.lines(), unbatched: [...this.runs.values()].filter((r) => !r.batch).length };
  }
  async listRuns(_scope: string, opts: ListRunsOptions = {}) {
    return [...this.runs.values()].filter((r) => (opts.batch === "none" ? !r.batch : true));
  }
  async listRunsPage(_scope: string, opts: ListRunsOptions = {}) {
    return { runs: [...this.runs.values()].filter((r) => r.batch?.id === opts.batch), next: null };
  }
  async getRun(_scope: string, id: string) {
    return this.runs.get(id)!;
  }
  async startRun(): Promise<SimulationRun> {
    throw new Error("not here");
  }
  async cancelRun(_scope: string, id: string) {
    return { run: this.runs.get(id)!, cancelled: false };
  }
  async moveRunsToBatch(_scope: string, batchId: string, runIds: readonly string[]): Promise<MoveRunsResponse> {
    this.calls.push(`move ${batchId} ${runIds.join(",")}`);
    const results = runIds.map((id) => {
      const r = this.runs.get(id)!;
      if (r.status === "running") return { run_id: id, status: "refused", code: "running", reason: "it is running" };
      const from = r.batch?.id;
      this.runs.set(id, { ...r, batch: { id: batchId } });
      return { run_id: id, status: "moved", ...(from ? { from } : {}) };
    });
    return { batch: { id: batchId, label: batchId, source: null }, results, counts: {}, bytes: 0 };
  }
  async removeRunsFromBatch(_scope: string, batchId: string, runIds: readonly string[]): Promise<RunAdminResponse> {
    this.calls.push(`remove ${batchId} ${runIds.join(",")}`);
    for (const id of runIds) this.runs.set(id, { ...this.runs.get(id)!, batch: null });
    return { results: runIds.map((id) => ({ run_id: id, status: "removed" })), counts: {}, bytes: 0 };
  }
  async deleteRuns(_scope: string, runIds: readonly string[], opts: { dry?: boolean } = {}): Promise<RunAdminResponse> {
    this.calls.push(`delete ${runIds.join(",")}${opts.dry ? " dry" : ""}`);
    if (!opts.dry) for (const id of runIds) this.runs.delete(id);
    return {
      results: runIds.map((id) => ({ run_id: id, status: "deleted", bytes: 100, ...(opts.dry ? { dry: true } : {}) })),
      counts: {},
      bytes: 100 * runIds.length,
    };
  }
  async deleteBatch(_scope: string, batchId: string, opts: { runs?: boolean; dry?: boolean } = {}): Promise<DeleteBatchResponse> {
    this.calls.push(`deleteBatch ${batchId} runs=${Boolean(opts.runs)}`);
    const members = [...this.runs.values()].filter((r) => r.batch?.id === batchId);
    for (const r of members) {
      if (opts.runs) this.runs.delete(r.run_id);
      else this.runs.set(r.run_id, { ...r, batch: null });
    }
    return {
      batch: { id: batchId, deleted: true, runs: members.length },
      results: members.map((r) => ({ run_id: r.run_id, status: opts.runs ? "deleted" : "removed" })),
      counts: {},
      bytes: 0,
    };
  }
}

let api: AdminApi;
let store: ReturnType<typeof createSimulationStore>;
const s = () => store.getState();
const noTimer = () => () => {};

beforeEach(async () => {
  api = new AdminApi();
  for (const r of [run("solo1", "done"), run("solo2", "done"), run("b1", "done", "frame"), run("b2", "done", "frame"), run("o1", "done", "other")]) {
    api.runs.set(r.run_id, r);
  }
  store = createSimulationStore(api, noTimer);
  await s().loadRuns("user:me");
  await s().loadBatchRuns("user:me", "frame");
});

const countOf = (id: string) => s().batches.find((b) => b.id === id)?.count;

test("moving runs of no batch into a batch: its line and its open page count them", async () => {
  const res = await s().moveRuns("user:me", "frame", ["solo1"]);
  assert.equal(res.results[0].status, "moved");
  assert.deepEqual(s().runs.map((r) => r.run_id), ["solo2"]);
  assert.equal(countOf("frame"), 3);
  assert.deepEqual(s().batchRuns.frame.runs.map((r) => r.run_id).sort(), ["b1", "b2", "solo1"]);
});

test("moving a run out of an open batch into another updates both", async () => {
  await s().moveRuns("user:me", "other", ["b1"], { replace: true });
  assert.equal(countOf("frame"), 1);
  assert.equal(countOf("other"), 2);
  assert.deepEqual(s().batchRuns.frame.runs.map((r) => r.run_id), ["b2"]);
});

test("taking runs out of a batch puts them back among the runs of no batch", async () => {
  await s().removeFromBatch("user:me", "frame", ["b1"]);
  assert.equal(countOf("frame"), 1);
  assert.ok(s().runs.some((r) => r.run_id === "b1"));
  assert.deepEqual(s().batchRuns.frame.runs.map((r) => r.run_id), ["b2"]);
});

test("deleting runs drops them everywhere; a dry run changes nothing", async () => {
  const dry = await s().deleteRuns("user:me", ["solo1", "b1"], { dry: true });
  assert.equal(dry.bytes, 200);
  assert.equal(s().runs.length, 2);
  assert.equal(countOf("frame"), 2);
  await s().deleteRuns("user:me", ["solo1", "b1"]);
  assert.deepEqual(s().runs.map((r) => r.run_id), ["solo2"]);
  assert.equal(countOf("frame"), 1);
  assert.deepEqual(s().batchRuns.frame.runs.map((r) => r.run_id), ["b2"]);
});

test("deleting a batch with its runs, or keeping them at the root", async () => {
  await s().deleteBatch("user:me", "frame", { runs: false });
  assert.equal(countOf("frame"), undefined);
  assert.equal(s().batchRuns.frame, undefined, "its page is dropped");
  assert.deepEqual(s().runs.map((r) => r.run_id).sort(), ["b1", "b2", "solo1", "solo2"]);
  await s().deleteBatch("user:me", "other", { runs: true });
  assert.equal(countOf("other"), undefined);
  assert.equal(api.runs.has("o1"), false);
  assert.equal(s().batches.length, 0);
});

test("a server without the routes says so instead of acting", async () => {
  const bare = createSimulationStore(
    {
      listProviders: api.listProviders.bind(api),
      listRuns: api.listRuns.bind(api),
      getRun: api.getRun.bind(api),
      startRun: api.startRun.bind(api),
      cancelRun: api.cancelRun.bind(api),
    },
    noTimer,
  );
  await assert.rejects(bare.getState().deleteRuns("user:me", ["solo1"]), /cannot manage runs/);
});
