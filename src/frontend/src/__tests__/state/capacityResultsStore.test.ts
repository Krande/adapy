import { beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  useCapacityResultsStore,
  WORST_CASE_ID,
  type CapacityResults,
} from "../../state/capacityResultsStore";
import type { CapacityManifest } from "../../services/viewerApi";

const MANIFEST: CapacityManifest = {
  version: 1,
  results_url: "capacity.results.json",
  default_run_id: "run-b",
};

const RESULTS: CapacityResults = {
  format: "dnv-rp-c201-capacity-results",
  version: 1,
  runs: [
    {
      id: "run-a",
      result_cases: [{ id: "1" }],
      capacity_models: [],
      case_results: [],
      visual_fields: [],
    },
    {
      id: "run-b",
      result_cases: [{ id: "7" }, { id: "8" }],
      capacity_models: [],
      case_results: [
        {
          id: "row-1",
          case_id: "7",
          capacity_model_id: "model-1",
          panel_group: "panel",
          governing_usage: 0.5,
          passed: true,
          checks: [
            {
              id: "plate",
              label: "Plate buckling",
              usage: 0.5,
              passed: true,
              intermediates: {
                lambda_p: 0.42,
                method: "SCM2",
              },
            },
          ],
        },
      ],
      visual_fields: [],
    },
  ],
};

describe("capacityResultsStore", () => {
  beforeEach(() => {
    useCapacityResultsStore.getState().clear();
  });

  // A multi-case run opens on the worst over all its cases: one arbitrary case
  // in isolation says nothing about which case governs. Applies to both run
  // types — the girder run reaches the same defaultCaseId().
  it("uses manifest.default_run_id and opens a multi-case run on Worst", () => {
    useCapacityResultsStore
      .getState()
      .setCapacityData(
        MANIFEST,
        { sourceName: "model.SIN", resultsUrl: "capacity.results.json" },
        RESULTS,
      );

    const state = useCapacityResultsStore.getState();
    assert.equal(state.activeRunId, "run-b"); // two cases: 7 and 8
    assert.equal(state.activeCaseId, WORST_CASE_ID);
    assert.deepEqual(state.worstCaseIds, ["7", "8"]);
    assert.equal(state.activeMetricId, "capacity.uf.governing");
    assert.equal(state.selectedResultId, null);
    assert.equal(state.error, null);
    assert.equal(state.loading, false);
  });

  it("opens a single-case run on that case, not on Worst", () => {
    useCapacityResultsStore
      .getState()
      .setCapacityData(
        { ...MANIFEST, default_run_id: "run-a" },
        { sourceName: "model.SIN", resultsUrl: "capacity.results.json" },
        RESULTS,
      );

    const state = useCapacityResultsStore.getState();
    assert.equal(state.activeRunId, "run-a"); // one case: 1
    assert.equal(state.activeCaseId, "1");
  });

  it("switching to a multi-case run lands on Worst too", () => {
    const store = () => useCapacityResultsStore.getState();
    store().setCapacityData(
      { ...MANIFEST, default_run_id: "run-a" },
      { sourceName: "model.SIN", resultsUrl: "capacity.results.json" },
      RESULTS,
    );
    assert.equal(store().activeCaseId, "1");

    store().setActiveRunId("run-b");
    assert.equal(store().activeCaseId, WORST_CASE_ID);

    // Returning to a run restores what was open there rather than re-defaulting.
    store().setActiveCaseId("8");
    store().setActiveRunId("run-a");
    store().setActiveRunId("run-b");
    assert.equal(store().activeCaseId, "8");
  });

  it("clears selected model when run or case changes", () => {
    useCapacityResultsStore
      .getState()
      .setCapacityData(
        MANIFEST,
        { sourceName: "model.SIN", resultsUrl: "capacity.results.json" },
        RESULTS,
      );
    useCapacityResultsStore
      .getState()
      .setSelectedCapacityResult("panel-1", "row-1");

    useCapacityResultsStore.getState().setActiveCaseId("8");
    assert.equal(useCapacityResultsStore.getState().selectedModelId, null);
    assert.equal(useCapacityResultsStore.getState().selectedResultId, null);

    useCapacityResultsStore
      .getState()
      .setSelectedCapacityResult("panel-2", "row-2");
    useCapacityResultsStore.getState().setActiveRunId("run-a");
    assert.equal(useCapacityResultsStore.getState().selectedModelId, null);
    assert.equal(useCapacityResultsStore.getState().selectedResultId, null);
  });

  it("clear resets optional filters and loaded data", () => {
    useCapacityResultsStore
      .getState()
      .setCapacityData(
        MANIFEST,
        { sourceName: "model.SIN", resultsUrl: "capacity.results.json" },
        RESULTS,
      );
    useCapacityResultsStore.getState().setFailedOnly(true);
    useCapacityResultsStore.getState().setError("bad sidecar");

    useCapacityResultsStore.getState().clear();

    const state = useCapacityResultsStore.getState();
    assert.equal(state.results, null);
    assert.equal(state.source, null);
    assert.equal(state.selectedResultId, null);
    assert.equal(state.failedOnly, false);
    assert.equal(state.error, null);
  });

  it("preserves per-check intermediate values from the sidecar", () => {
    useCapacityResultsStore
      .getState()
      .setCapacityData(
        MANIFEST,
        { sourceName: "model.SIN", resultsUrl: "capacity.results.json" },
        RESULTS,
      );

    const check =
      useCapacityResultsStore.getState().results!.runs[1].case_results[0]
        .checks[0];
    assert.deepEqual(check.intermediates, {
      lambda_p: 0.42,
      method: "SCM2",
    });
  });

  // v14: the worst summary arrives as one shard per case rather than a single
  // whole-run file, which on large models exceeded the max string length and
  // made the decode throw (the worst table then rendered empty).
  it("accumulates worst-summary shards case by case", () => {
    const store = () => useCapacityResultsStore.getState();
    store().setCapacityData(
      MANIFEST,
      { sourceName: "model.SIN", resultsUrl: "capacity.results.json" },
      RESULTS,
    );
    assert.equal(store().worstSummary, null);

    store().mergeWorstSummaryCases({
      "7": { label: "Case 7", rows: [{ m: "model-1", pg: "panel", u: 0.5, p: true }] },
    });
    store().mergeWorstSummaryCases({
      "8": { label: "Case 8", rows: [{ m: "model-1", pg: "panel", u: 0.9, p: false }] },
    });

    assert.deepEqual(Object.keys(store().worstSummary!.cases).sort(), ["7", "8"]);
    assert.equal(store().worstSummary!.cases["8"].rows[0].u, 0.9);
    // A re-fetched case replaces its own bucket without disturbing the others.
    store().mergeWorstSummaryCases({
      "7": { label: "Case 7", rows: [] },
    });
    assert.equal(store().worstSummary!.cases["7"].rows.length, 0);
    assert.equal(store().worstSummary!.cases["8"].rows.length, 1);
  });

  it("drops accumulated worst-summary shards on clear", () => {
    useCapacityResultsStore.getState().mergeWorstSummaryCases({
      "7": { label: "Case 7", rows: [] },
    });
    useCapacityResultsStore.getState().setWorstSummaryError("partial load");
    useCapacityResultsStore.getState().clear();

    assert.equal(useCapacityResultsStore.getState().worstSummary, null);
    assert.equal(useCapacityResultsStore.getState().worstSummaryError, null);
  });
});

describe("capacity worst-case subset defaults", () => {
  beforeEach(() => {
    useCapacityResultsStore.getState().clear();
  });

  /** A run's cases only exist once its check has published them, so a run can
   *  be selected while it still has none. */
  const streaming = (runCases: string[]): CapacityResults => ({
    ...RESULTS,
    runs: [
      { ...RESULTS.runs[0] },
      {
        ...RESULTS.runs[1],
        result_cases: runCases.map((id) => ({ id })),
      },
    ],
  });

  it("includes every case a run gains while the calculation runs", () => {
    const store = useCapacityResultsStore.getState();
    store.setCapacityData(MANIFEST, { sourceName: "m.SIN", resultsUrl: "u" }, streaming([]));
    assert.deepEqual(useCapacityResultsStore.getState().worstCaseIds, []);

    useCapacityResultsStore.getState().refreshResults(streaming(["7", "8"]));
    assert.deepEqual(useCapacityResultsStore.getState().worstCaseIds, ["7", "8"]);
  });

  it("selects every case of a run first visited before it had any", () => {
    // Switching away stashes the run's state; restoring an empty subset left
    // the worst table empty for the rest of the session.
    const store = useCapacityResultsStore.getState();
    store.setCapacityData(MANIFEST, { sourceName: "m.SIN", resultsUrl: "u" }, streaming([]));
    useCapacityResultsStore.getState().setActiveRunId("run-a");
    useCapacityResultsStore.getState().refreshResults(streaming(["7", "8"]));
    useCapacityResultsStore.getState().setActiveRunId("run-b");

    assert.deepEqual(useCapacityResultsStore.getState().worstCaseIds, ["7", "8"]);
  });

  it("keeps a subset the user chose, and still adopts new cases into it", () => {
    const store = useCapacityResultsStore.getState();
    store.setCapacityData(MANIFEST, { sourceName: "m.SIN", resultsUrl: "u" }, streaming(["7", "8"]));
    useCapacityResultsStore.getState().setWorstCaseIds(["7"]);

    useCapacityResultsStore.getState().refreshResults(streaming(["7", "8", "9"]));
    assert.deepEqual(useCapacityResultsStore.getState().worstCaseIds, ["7", "9"]);

    useCapacityResultsStore.getState().setActiveRunId("run-a");
    useCapacityResultsStore.getState().setActiveRunId("run-b");
    assert.deepEqual(useCapacityResultsStore.getState().worstCaseIds, ["7", "9"]);
  });
});
