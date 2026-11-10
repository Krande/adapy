// The simulation store against a fake API and a fake timer: providers, the scope's runs, and
// the polling that follows a run until it ends.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type {
  SimulationDomain,
  SimulationProviderInfo,
  SimulationRun,
  SimulationRunStatus,
} from "@/services/api/simulations";

// The API module reads the runtime config, which reads `window`.
const g = globalThis as Record<string, unknown>;
g.window ??= { location: new URL("http://localhost/"), addEventListener() {}, removeEventListener() {} };

const { createSimulationStore, providersByDomain, upsertRun, SIMULATION_POLL_MS } = await import(
  "@/state/simulationStore"
);
type Store = ReturnType<typeof createSimulationStore>;

const DOMAINS: SimulationDomain[] = [
  { id: "fea", title: "FEA", description: "", artefact_roles: [] },
  { id: "robotics", title: "Robotics", description: "", artefact_roles: [] },
  { id: "cfd", title: "CFD", description: "", artefact_roles: [] },
];

function provider(id: string, domain: string, online = true): SimulationProviderInfo {
  return {
    id,
    domain,
    engine: "fake",
    title: id,
    version: "1",
    description: null,
    kinds: [],
    parameters_schema: {},
    capability: `sim-${id}`,
    online,
    workers: online ? 1 : 0,
  };
}

function run(id: string, status: SimulationRunStatus, progress = 0, over: Partial<SimulationRun> = {}): SimulationRun {
  return {
    run_id: id,
    provider: { id: "arm", domain: "robotics", engine: "fake", version: "1" },
    kind: null,
    parameters: {},
    inputs: [],
    started_by: { id: "me", display: null },
    created_at: "2024-01-01T00:00:00.000Z",
    started_at: null,
    finished_at: null,
    status,
    stage: status,
    progress,
    error: null,
    job_id: `job-${id}`,
    artefacts: [],
    summary: {},
    cancel_requested: false,
    ...over,
  };
}

/** A server: runs by scope, each `getRun` answering the next state queued for that run. */
class FakeApi {
  calls: string[] = [];
  runs = new Map<string, SimulationRun[]>();
  next = new Map<string, SimulationRun[]>();
  providersError: string | null = null;
  startError: string | null = null;

  async listProviders() {
    this.calls.push("listProviders");
    if (this.providersError) throw new Error(this.providersError);
    return { domains: DOMAINS, providers: [provider("beam", "fea"), provider("arm", "robotics"), provider("old", "robotics", false)] };
  }
  async listRuns(scope: string) {
    this.calls.push(`listRuns ${scope}`);
    return [...(this.runs.get(scope) ?? [])];
  }
  async getRun(scope: string, runId: string) {
    this.calls.push(`getRun ${scope} ${runId}`);
    const queue = this.next.get(runId) ?? [];
    const state = queue.length > 1 ? queue.shift()! : queue[0];
    if (!state) throw new Error("no such run");
    return state;
  }
  async startRun(scope: string, body: { provider: string }) {
    this.calls.push(`startRun ${scope} ${body.provider}`);
    if (this.startError) throw new Error(this.startError);
    return run("new", "queued");
  }
  async cancelRun(scope: string, runId: string) {
    this.calls.push(`cancelRun ${scope} ${runId}`);
    return { run: run(runId, "running", 0.4, { cancel_requested: true }), cancelled: true };
  }
}

/** A timer that runs when told to. */
class FakeTimer {
  pending: Array<{ fn: () => void; ms: number; cancelled: boolean }> = [];
  schedule = (fn: () => void, ms: number) => {
    const entry = { fn, ms, cancelled: false };
    this.pending.push(entry);
    return () => {
      entry.cancelled = true;
    };
  };
  get armed() {
    return this.pending.filter((p) => !p.cancelled).length;
  }
  /** Fire the armed timer and wait for the round it starts. */
  async fire() {
    const entry = this.pending.find((p) => !p.cancelled);
    assert.ok(entry, "no timer armed");
    this.pending = this.pending.filter((p) => p !== entry);
    entry.fn();
    await new Promise((r) => setTimeout(r, 0));
  }
}

let api: FakeApi;
let timer: FakeTimer;
let store: Store;
const s = () => store.getState();

beforeEach(() => {
  api = new FakeApi();
  timer = new FakeTimer();
  store = createSimulationStore(api, timer.schedule);
});

test("providers and domains are loaded, and grouped by domain in the server's order", async () => {
  await s().loadProviders();
  assert.equal(s().providersLoaded, true);
  assert.deepEqual(s().domains.map((d) => d.id), ["fea", "robotics", "cfd"]);
  const groups = providersByDomain(s().domains, s().providers);
  assert.deepEqual(
    groups.map((g) => [g.domain.title, g.providers.map((p) => `${p.id}:${p.online ? "online" : "offline"}`)]),
    [
      ["FEA", ["beam:online"]],
      ["Robotics", ["arm:online", "old:offline"]],
    ],
    "a domain without providers is left out; an offline provider is still listed",
  );
});

test("a provider of a domain the server did not list gets a group of its own, last", () => {
  const groups = providersByDomain(DOMAINS, [provider("x", "thermal"), provider("beam", "fea")]);
  assert.deepEqual(groups.map((g) => g.domain.id), ["fea", "thermal"]);
});

test("a failed provider refresh keeps the last list and says why", async () => {
  await s().loadProviders();
  api.providersError = "503 Service Unavailable";
  await s().loadProviders();
  assert.equal(s().providers.length, 3);
  assert.equal(s().providersError, "503 Service Unavailable");
});

test("loading a scope's finished runs arms no timer", async () => {
  api.runs.set("shared", [run("a", "done", 1), run("b", "error")]);
  await s().loadRuns("shared");
  assert.deepEqual(s().runs.map((r) => r.run_id), ["a", "b"]);
  assert.equal(timer.armed, 0);
});

test("an unfinished run is polled until it ends, and only it", async () => {
  api.runs.set("shared", [run("live", "running", 0.1), run("old", "done", 1)]);
  const partial = { name: "timeline.json", key: "k", media_type: "application/json", role: "timeline", size: 3, partial: true, revision: 2 };
  api.next.set("live", [
    run("live", "running", 0.5, { stage: "step 2/4", artefacts: [partial] }),
    run("live", "done", 1, { artefacts: [{ ...partial, partial: false, revision: 5 }] }),
  ]);
  await s().loadRuns("shared");
  assert.equal(timer.armed, 1);
  assert.equal(timer.pending[0].ms, SIMULATION_POLL_MS);

  await timer.fire();
  const live = s().runs.find((r) => r.run_id === "live")!;
  assert.deepEqual([live.status, live.progress, live.stage], ["running", 0.5, "step 2/4"]);
  assert.equal(live.artefacts[0].partial, true, "the partial artefact is listed while the run is going");
  assert.equal(timer.armed, 1, "still going: armed again");

  await timer.fire();
  assert.equal(s().runs.find((r) => r.run_id === "live")!.status, "done");
  assert.equal(timer.armed, 0, "ended: polling stops");
  assert.deepEqual(
    api.calls.filter((c) => c.startsWith("getRun")),
    ["getRun shared live", "getRun shared live"],
    "the finished run was never asked for",
  );
  assert.deepEqual(s().runs.map((r) => r.run_id), ["live", "old"], "the order is kept");
});

test("a run that cannot be read in one round keeps its state and is tried again", async () => {
  api.runs.set("shared", [run("live", "running", 0.2)]);
  await s().loadRuns("shared");
  await timer.fire(); // getRun throws: nothing queued for it
  assert.equal(s().runs[0].progress, 0.2);
  assert.equal(timer.armed, 1);
});

test("starting a run puts it first and follows it", async () => {
  api.runs.set("shared", [run("old", "done", 1)]);
  await s().loadRuns("shared");
  api.next.set("new", [run("new", "done", 1)]);
  const started = await s().startRun("shared", { provider: "arm", parameters: { steps: 2 } });
  assert.equal(started?.run_id, "new");
  assert.deepEqual(s().runs.map((r) => r.run_id), ["new", "old"]);
  assert.deepEqual([s().starting, s().startError], [false, null]);
  assert.equal(timer.armed, 1);
  await timer.fire();
  assert.equal(s().runs[0].status, "done");
});

test("a refused start says why and adds no run", async () => {
  api.startError = "startSimulation failed: 400 Bad Request — invalid parameters: steps: is required";
  assert.equal(await s().startRun("shared", { provider: "arm" }), null);
  assert.match(s().startError!, /steps: is required/);
  assert.deepEqual([s().starting, s().runs.length, timer.armed], [false, 0, 0]);
});

test("cancelling shows the run as asked to stop and keeps following it", async () => {
  api.runs.set("shared", [run("live", "running", 0.3)]);
  api.next.set("live", [run("live", "cancelled", 0.4)]);
  await s().loadRuns("shared");
  await s().cancelRun("shared", "live");
  assert.equal(s().runs[0].cancel_requested, true);
  assert.equal(timer.armed, 1, "one timer, not one per action");
  await timer.fire();
  assert.equal(s().runs[0].status, "cancelled");
  assert.equal(timer.armed, 0);
});

test("switching scope drops the old runs, and a late answer for the old scope is ignored", async () => {
  api.runs.set("shared", [run("a", "running", 0.1)]);
  api.runs.set("user:me", [run("b", "done", 1)]);
  api.next.set("a", [run("a", "running", 0.9)]);
  await s().loadRuns("shared");
  const round = s().pollOnce(); // in flight for "shared"...
  await s().loadRuns("user:me"); // ...while the scope changes
  assert.equal(await round, false);
  assert.deepEqual([s().runsScope, s().runs.map((r) => r.run_id)], ["user:me", ["b"]]);
});

test("stopPolling disarms the timer and keeps the list", async () => {
  api.runs.set("shared", [run("live", "running")]);
  await s().loadRuns("shared");
  s().stopPolling();
  assert.equal(timer.armed, 0);
  assert.equal(s().runs.length, 1);
});

test("the panel flag is a plain toggle", () => {
  s().setPanelOpen(true);
  assert.equal(s().panelOpen, true);
});

test("upsertRun replaces in place or puts a new run first", () => {
  const list = [run("a", "running"), run("b", "done")];
  assert.deepEqual(upsertRun(list, run("b", "error")).map((r) => `${r.run_id}:${r.status}`), ["a:running", "b:error"]);
  assert.deepEqual(upsertRun(list, run("c", "queued")).map((r) => r.run_id), ["c", "a", "b"]);
});
