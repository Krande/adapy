import type {CapacityResults} from "@/state/capacityResultsStore";
import type {FeaManifest} from "@/services/viewerApi";

export const CAPACITY_RESULTS_FORMAT = "dnv-rp-c201-capacity-results";
// v2: stations. v3: [6.4.3] discretization. v4/v5: per-stiffener tributary plates.
// v6: scalable bundle — the spine keeps only models/catalog/result cases/field
// metadata; per-element UF values move to AFEL colour blobs (Range-fetched one
// (field, case) step at a time) and per-(case, model, stiffener) rows move to
// per-case detail files (capacity.detail/<case>.json, fetched on demand).
// v7: add the worst-over-cases summary (capacity.summary.json) + trim detail
// resolved_vectors. Kept in lockstep with viewer_export.py CAPACITY_RESULTS_VERSION.
export const CAPACITY_RESULTS_VERSION = 7;

export interface CapacityValidationContext {
    manifest?: Pick<FeaManifest, "source_sha256"> | null;
}

export function validateCapacityResults(
    results: CapacityResults,
    context: CapacityValidationContext = {},
): void {
    if (!results || typeof results !== "object") {
        throw new Error("capacity results must be an object");
    }
    if (results.format !== CAPACITY_RESULTS_FORMAT) {
        throw new Error(`unsupported capacity results format ${String(results.format)}`);
    }
    if (results.version !== CAPACITY_RESULTS_VERSION) {
        throw new Error(`unsupported capacity results version ${String(results.version)}`);
    }
    if (!Array.isArray(results.runs) || results.runs.length === 0) {
        throw new Error("capacity results contain no runs");
    }

    const manifestHash = normalizeSha256(context.manifest?.source_sha256);
    const sidecarHash = normalizeSha256(readSidecarSourceHash(results));
    if (manifestHash && sidecarHash && manifestHash !== sidecarHash) {
        throw new Error(
            "capacity results are stale for this source: "
            + `manifest source_sha256=${manifestHash}, sidecar source.sin_sha256=${sidecarHash}`,
        );
    }
}

function readSidecarSourceHash(results: CapacityResults): string | null {
    const source = results.source;
    if (!source || typeof source !== "object") return null;
    const value = source.sin_sha256;
    return typeof value === "string" ? value : null;
}

function normalizeSha256(value: string | null | undefined): string | null {
    const clean = value?.trim().toLowerCase() ?? "";
    if (!clean) return null;
    return clean;
}
