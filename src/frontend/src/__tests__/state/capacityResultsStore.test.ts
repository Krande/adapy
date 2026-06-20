import {beforeEach, describe, it} from "node:test";
import assert from "node:assert/strict";

import {
    useCapacityResultsStore,
    type CapacityResults,
} from "../../state/capacityResultsStore";
import type {CapacityManifest} from "../../services/viewerApi";

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
            result_cases: [{id: "1"}],
            capacity_models: [],
            case_results: [],
            visual_fields: [],
        },
        {
            id: "run-b",
            result_cases: [{id: "7"}, {id: "8"}],
            capacity_models: [],
            case_results: [],
            visual_fields: [],
        },
    ],
};

describe("capacityResultsStore", () => {
    beforeEach(() => {
        useCapacityResultsStore.getState().clear();
    });

    it("uses manifest.default_run_id and the first case as active state", () => {
        useCapacityResultsStore.getState().setCapacityData(
            MANIFEST,
            {sourceName: "model.SIN", resultsUrl: "capacity.results.json"},
            RESULTS,
        );

        const state = useCapacityResultsStore.getState();
        assert.equal(state.activeRunId, "run-b");
        assert.equal(state.activeCaseId, "7");
        assert.equal(state.activeMetricId, "capacity.uf.governing");
        assert.equal(state.selectedResultId, null);
        assert.equal(state.error, null);
        assert.equal(state.loading, false);
    });

    it("clears selected model when run or case changes", () => {
        useCapacityResultsStore.getState().setCapacityData(
            MANIFEST,
            {sourceName: "model.SIN", resultsUrl: "capacity.results.json"},
            RESULTS,
        );
        useCapacityResultsStore.getState().setSelectedCapacityResult("panel-1", "row-1");

        useCapacityResultsStore.getState().setActiveCaseId("8");
        assert.equal(useCapacityResultsStore.getState().selectedModelId, null);
        assert.equal(useCapacityResultsStore.getState().selectedResultId, null);

        useCapacityResultsStore.getState().setSelectedCapacityResult("panel-2", "row-2");
        useCapacityResultsStore.getState().setActiveRunId("run-a");
        assert.equal(useCapacityResultsStore.getState().selectedModelId, null);
        assert.equal(useCapacityResultsStore.getState().selectedResultId, null);
    });

    it("clear resets optional filters and loaded data", () => {
        useCapacityResultsStore.getState().setCapacityData(
            MANIFEST,
            {sourceName: "model.SIN", resultsUrl: "capacity.results.json"},
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
});
