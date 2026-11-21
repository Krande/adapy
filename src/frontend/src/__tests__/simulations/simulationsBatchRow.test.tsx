// The Robotic Simulations panel's runs list with batches: a batch is one line (label, provider, how
// many runs and in which status, its progress), closed at first; opened, its runs are listed under
// it, indented, each the run line a run of no batch has, with the cancelled ones behind a toggle and
// the rest of a long batch behind "Show more".
import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { SimulationsBody } = await import("@/components/simulations/SimulationsPanel");
const { useSimulationStore } = await import("@/state/simulationStore");
const { resetOpenBatches, setBatchOpen } = await import("@/services/simulations/batchRows");

const run = (id: string, status = "done", at = "2026-11-18T08:00:00Z") =>
  ({
    run_id: id,
    status,
    provider: { id: "arm", domain: "robotics" },
    parameters: { label: `frame ${id}` },
    artefacts: [],
    summary: {},
    progress: 0.5,
    created_at: at,
    batch: null,
  }) as never;

const frame = {
  id: "frame-1",
  label: "Frame A",
  source: { kind: "group", key: "g1" },
  provider: { id: "arm", domain: "robotics" },
  providers: [{ id: "arm", domain: "robotics" }],
  count: 771,
  counts: { done: 86, running: 4, queued: 300, error: 0, cancelled: 381 },
  active: 304,
  progress: 0.22,
  newest_at: "2026-11-18T09:00:00Z",
  oldest_at: "2026-11-18T07:00:00Z",
};

const render = () =>
  renderToStaticMarkup(
    React.createElement(AdaViewerProvider, null, React.createElement(SimulationsBody, { initialRunsOpen: true })),
  );

const state = (over: Record<string, unknown> = {}) =>
  setServerState(useSimulationStore, {
    runs: [run("solo", "done", "2026-11-18T10:00:00Z")],
    batches: [frame],
    batchRuns: {},
    providers: [{ id: "arm", domain: "robotics", title: "Robot cell", engine: "fake", kinds: [], parameters_schema: {}, online: true }],
    loadProviders: async () => {},
    loadRuns: async () => {},
    loadBatchRuns: async () => {},
    ...over,
  } as never);

test("a batch is one line, closed at first, that says its runs by status", () => {
  resetOpenBatches();
  state();
  const html = render();
  assert.match(html, /data-batch-id="frame-1"/);
  assert.match(html, /aria-expanded="false"[^>]*data-batch-toggle/);
  assert.match(html, />Frame A</);
  assert.match(html, /771 runs/);
  assert.match(html, /Robot cell/);
  assert.match(html, /86 done · 4 running · 300 queued · 381 cancelled/);
  assert.match(html, />22%</);
  assert.doesNotMatch(html, /data-batch-runs/, "its runs are not drawn while it is closed");
  // The newer run of no batch comes first; the heading counts every run.
  assert.ok(html.indexOf('data-run-id="solo"') < html.indexOf('data-batch-id="frame-1"'));
  assert.match(html, />Runs \(772\)</);
});

test("an open batch lists its runs under it, with the cancelled toggle and the rest behind Show more", () => {
  resetOpenBatches();
  setBatchOpen("shared", "frame-1", true);
  state({
    runsScope: "shared",
    batchRuns: {
      "frame-1": { runs: [run("r2", "running"), run("r1", "queued")], next: "r1", loading: false, error: null, cancelled: false },
    },
  });
  const html = render();
  assert.match(html, /aria-expanded="true"[^>]*data-batch-toggle/);
  const inner = html.slice(html.indexOf("data-batch-runs"));
  assert.match(inner, /data-run-id="r2"/);
  assert.match(inner, /data-run-id="r1"/);
  assert.match(inner, /Show cancelled \(381\)/);
  assert.match(inner, /data-batch-more[^>]*>Show more \(388\)/);
});
