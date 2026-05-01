// Public surface of the conversion subsystem. Routes between the
// in-browser Pyodide pipeline and the server-side NATS pipeline.

import {useExperimentalStore} from "@/state/experimentalStore";
import {runtime} from "@/runtime/config";
import {viewerApi, TargetFormat, ScopeUrl} from "@/services/viewerApi";
import {convertViaPyodideAndUpload} from "./pyodidePipeline";
import {convertViaServer} from "./serverPipeline";

export type {TargetFormat} from "@/services/viewerApi";

function shouldUsePyodide(sourceKey: string, targetFormat: TargetFormat): boolean {
    if (targetFormat !== "glb") return false;
    const lower = sourceKey.toLowerCase();
    // .ifc → ifcopenshell wasm wheel + trimesh; .step/.stp → adacpp wasm
    // wheel + adapy.cad. Both share one Pyodide worker, lazy-initialised
    // per stack so the unused format never pays its install cost.
    const supported = lower.endsWith(".ifc")
        || lower.endsWith(".step")
        || lower.endsWith(".stp");
    if (!supported) return false;
    return useExperimentalStore.getState().pyodideConverter;
}

/**
 * Enqueue a conversion for a source key in a given scope and resolve
 * when the derived blob is ready. Returns the derived storage key.
 *
 * Routes through the Pyodide in-browser path when the experimental
 * toggle is on AND the source/target combination is supported there;
 * otherwise hits the server-side NATS pipeline. Throws on API
 * rejection, job error, or poll-timeout.
 *
 * ``opts.step``/``opts.field`` are forwarded to the server for FEA
 * result picks. The Pyodide path doesn't support FEA results, so they
 * route to the server pipeline regardless of the experimental toggle.
 */
export async function ensureConverted(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat = "glb",
    opts?: {step?: number; field?: string},
): Promise<string> {
    if (shouldUsePyodide(sourceKey, targetFormat)) {
        return convertViaPyodideAndUpload(scope, sourceKey);
    }
    if (!runtime.convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }
    return convertViaServer(scope, sourceKey, targetFormat, opts);
}

// Backwards-compatible wrapper for the GLB-for-viewing flow.
export async function ensureConvertedGlb(scope: ScopeUrl, sourceKey: string): Promise<void> {
    await ensureConverted(scope, sourceKey, "glb");
}

export async function fetchSupportedTargets(
    scope: ScopeUrl,
    sourceKey: string,
): Promise<TargetFormat[]> {
    return viewerApi.convertTargets(scope, sourceKey);
}
