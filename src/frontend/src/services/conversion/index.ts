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
    if (!sourceKey.toLowerCase().endsWith(".ifc")) return false;
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
 */
export async function ensureConverted(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat = "glb",
): Promise<string> {
    if (shouldUsePyodide(sourceKey, targetFormat)) {
        return convertViaPyodideAndUpload(scope, sourceKey);
    }
    if (!runtime.convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }
    return convertViaServer(scope, sourceKey, targetFormat);
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
