// The runs list's management: a Select toggle in its heading, a "Delete batch…" on an open batch,
// the bar of what can be done to the picked runs (Remove from batch only when one is in a batch),
// checkboxes on the runs while picking, and the batch delete dialog's choice.
import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { SimulationsBody, BatchRow } = await import("@/components/simulations/SimulationsPanel");
const { DeleteBatchDialog, MoveToBatchDialog, RunSelectContext, SelectionBar } = await import("@/components/simulations/RunAdmin");
const { useSimulationStore } = await import("@/state/simulationStore");
const { resetOpenBatches, setBatchOpen } = await import("@/services/simulations/batchRows");

const run = (id: string, batch: string | null = null, status = "done") =>
  ({
    run_id: id,
    status,
    provider: { id: "arm", domain: "robotics" },
    parameters: { label: `frame ${id}` },
    artefacts: [],
    summary: {},
    progress: 0,
    created_at: "2026-11-18T08:00:00Z",
    batch: batch ? { id: batch } : null,
  }) as never;

const frame = {
  id: "frame-1",
  label: "Frame A",
  source: null,
  provider: { id: "arm", domain: "robotics" },
  providers: [{ id: "arm", domain: "robotics" }],
  count: 2,
  counts: { done: 1, running: 1, queued: 0, error: 0, cancelled: 0 },
  active: 1,
  progress: 0.5,
  newest_at: "2026-11-18T09:00:00Z",
  oldest_at: "2026-11-18T07:00:00Z",
} as never;

const state = (over: Record<string, unknown> = {}) =>
  setServerState(useSimulationStore, {
    runs: [run("solo")],
    batches: [frame],
    batchRuns: {},
    providers: [],
    loadProviders: async () => {},
    loadRuns: async () => {},
    loadBatchRuns: async () => {},
    ...over,
  } as never);

const wrap = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));

test("the runs heading has a Select toggle, and an open batch a Delete batch action", () => {
  resetOpenBatches();
  setBatchOpen("shared", "frame-1", true);
  state({
    runsScope: "shared",
    batchRuns: { "frame-1": { runs: [run("b1", "frame-1")], next: null, loading: false, error: null, cancelled: false } },
  });
  const html = wrap(React.createElement(SimulationsBody, { initialRunsOpen: true }));
  assert.match(html, /data-runs-select[^>]*>Select</);
  assert.match(html, /data-batch-delete[^>]*>Delete batch…</);
  // Not picking: no checkboxes, no bar.
  assert.doesNotMatch(html, /data-run-select/);
  assert.doesNotMatch(html, /data-selection-bar/);
});

test("while picking, each run has a checkbox and a picked one says so", () => {
  resetOpenBatches();
  state();
  const api = {
    selecting: true,
    isSelected: (id: string) => id === "b1",
    click() {},
    longPress() {},
    report() {},
  };
  const html = wrap(
    React.createElement(
      RunSelectContext.Provider,
      { value: api },
      React.createElement(
        "ul",
        null,
        React.createElement(BatchRow, { scope: "shared", batch: frame, providers: [], expandedRun: null, onToggleRun() {} }),
      ),
    ),
  );
  assert.match(html, /data-batch-id="frame-1"/);
  setBatchOpen("shared", "frame-1", true);
  state({ batchRuns: { "frame-1": { runs: [run("b1", "frame-1"), run("b2", "frame-1")], next: null, loading: false, error: null, cancelled: false } } });
  const open = wrap(
    React.createElement(
      RunSelectContext.Provider,
      { value: api },
      React.createElement("ul", null, React.createElement(BatchRow, { scope: "shared", batch: frame, providers: [], expandedRun: null, onToggleRun() {} })),
    ),
  );
  assert.equal((open.match(/data-run-select/g) ?? []).length, 2);
  assert.match(open, /data-run-id="b1"[^>]*data-selected="true"/);
  assert.doesNotMatch(open, /data-run-id="b2"[^>]*data-selected/);
});

test("the bar counts what is picked, and Remove from batch needs a run in a batch", () => {
  const bar = (selected: never[]) =>
    wrap(
      React.createElement(SelectionBar, {
        scope: "shared",
        selected,
        count: selected.length,
        viewCount: 5,
        allInView: false,
        onSelectAll() {},
        onDone() {},
        onMove() {},
        onActed() {},
      }),
    );
  const loose = bar([run("a"), run("b")]);
  assert.match(loose, /data-selection-count[^>]*>2 selected</);
  assert.match(loose, /disabled=""[^>]*data-action-remove/);
  assert.match(loose, /Select all in view/);
  const batched = bar([run("a", "frame-1")]);
  assert.doesNotMatch(batched, /disabled=""[^>]*data-action-remove/);
});

test("the dialogs: delete a batch keeping or deleting its runs; move to a batch or a new one", () => {
  const del = wrap(React.createElement(DeleteBatchDialog, { scope: "shared", batch: frame, onClose() {} }));
  assert.match(del, /Delete batch &quot;Frame A&quot; \(2 runs\)\?/);
  assert.match(del, /Keep runs/);
  assert.match(del, /Delete runs too/);
  assert.match(del, /1 of its runs is still going/);
  const move = wrap(
    React.createElement(MoveToBatchDialog, {
      scope: "shared",
      runs: [run("a", "other"), run("b")],
      batches: [frame],
      onClose() {},
      onMoved() {},
    }),
  );
  assert.match(move, /Move 2 runs to a batch/);
  assert.match(move, /Frame A \(2 runs\)/);
  assert.match(move, /New batch…/);
  assert.match(move, /1 of them is in another batch/);
});
