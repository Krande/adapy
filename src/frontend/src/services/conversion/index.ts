// Public surface of the conversion subsystem. Routes between the
// in-browser Pyodide pipeline and the server-side NATS pipeline.

import {useConversionStore} from "@/state/conversionStore";
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
    opts?: {
        step?: number;
        field?: string;
        conversionOptions?: Partial<Record<
            "use_sat_pcurves" | "pcurve_drive_edge" | "skip_shapefix" | "merge_meshes" | "profile_conversions",
            boolean | null
        >>;
    },
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

/**
 * Eagerly enqueue the streaming-FEA manifest bake for a source file.
 *
 * Symmetric to ``ensureConvertedGlb`` but targets the /fea/manifest
 * pipeline used by streaming-only formats (.rmed, .odb, ...) — those
 * don't go through the legacy single-GLB path. Wires progress into
 * ``useConversionStore`` so the bottom-right ConversionProgress toast
 * tracks the bake the same way it tracks GLB conversions.
 *
 * Store key follows the ``${sourceKey}::${target}`` convention from
 * serverPipeline (target ``fea`` here) so a manifest bake and a GLB
 * conversion of the same source can coexist without colliding.
 */
export async function ensureBakedFeaManifest(
    scope: ScopeUrl,
    sourceKey: string,
): Promise<void> {
    const convStore = useConversionStore.getState();
    const storeKey = `${sourceKey}::fea`;
    const startedAt = Date.now();
    convStore.setJob(storeKey, {
        sourceKey,
        jobId: "",
        derivedKey: "",
        status: "queued",
        progress: 0,
        stage: "queuing fea bake",
        error: null,
        startedAt,
    });
    try {
        await viewerApi.feaManifest(scope, sourceKey, {
            onProgress: ({jobId, stage, progress, status}) => {
                convStore.setJob(storeKey, {
                    sourceKey,
                    jobId,
                    derivedKey: "",
                    status,
                    progress,
                    stage,
                    error: null,
                    startedAt,
                });
            },
        });
        convStore.setJob(storeKey, {
            sourceKey,
            jobId: "",
            derivedKey: "",
            status: "done",
            progress: 1,
            stage: "done",
            error: null,
            startedAt,
        });
    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        convStore.setJob(storeKey, {
            sourceKey,
            jobId: "",
            derivedKey: "",
            status: "error",
            progress: 0,
            stage: "error",
            error: msg,
            startedAt,
        });
        throw err;
    }
}

export async function fetchSupportedTargets(
    scope: ScopeUrl,
    sourceKey: string,
): Promise<TargetFormat[]> {
    return viewerApi.convertTargets(scope, sourceKey);
}
