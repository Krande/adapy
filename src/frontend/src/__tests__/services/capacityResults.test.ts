import {describe, it} from "node:test";
import assert from "node:assert/strict";

import {
    CAPACITY_RESULTS_FORMAT,
    validateCapacityResults,
} from "../../services/capacityResults";
import type {CapacityResults} from "../../state/capacityResultsStore";
import type {FeaManifest} from "../../services/viewerApi";

function payload(overrides: Partial<CapacityResults> = {}): CapacityResults {
    return {
        format: CAPACITY_RESULTS_FORMAT,
        version: 2,
        source: {
            sin_name: "model.SIN",
            sin_sha256: "abc123",
        },
        runs: [
            {
                id: "run-001",
                result_cases: [{id: "10"}],
                capacity_models: [
                    {
                        id: "model-1",
                        panel_group: "panel",
                        type: "stiffened_panel",
                        element_ids: {all: [101]},
                    },
                ],
                case_results: [],
                visual_fields: [],
            },
        ],
        ...overrides,
    };
}

describe("validateCapacityResults", () => {
    it("accepts a v2 capacity sidecar", () => {
        assert.doesNotThrow(() => validateCapacityResults(payload()));
    });

    it("rejects unsupported versions", () => {
        assert.throws(
            () => validateCapacityResults(payload({version: 1})),
            /unsupported capacity results version 1/,
        );
    });

    it("rejects sidecars without runs", () => {
        assert.throws(
            () => validateCapacityResults(payload({runs: []})),
            /capacity results contain no runs/,
        );
    });

    it("rejects stale sidecars when the manifest and sidecar hashes disagree", () => {
        const manifest = {source_sha256: "def456"} as FeaManifest;
        assert.throws(
            () => validateCapacityResults(payload(), {manifest}),
            /capacity results are stale/,
        );
    });

    it("does not require hashes on legacy manifests or sidecars", () => {
        const manifest = {} as FeaManifest;
        assert.doesNotThrow(() => validateCapacityResults(payload({source: {sin_name: "model.SIN"}}), {manifest}));
    });
});
