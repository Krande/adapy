// Public surface of the conversion subsystem. Routes between the
// in-browser Pyodide pipeline and the server-side NATS pipeline.

import {useConversionStore} from "@/state/conversionStore";
import {runtime} from "@/runtime/config";
import {viewerApi, TargetFormat, ScopeUrl} from "@/services/viewerApi";
import {workerPreferred} from "@/services/compute/workerAccess";
import {convertViaServer} from "./serverPipeline";
import {wasmSupportsConversion, isWasmFeaSource} from "./wasmSupport";
import {serializerSchemaFor, normalizeSelection, SerializerSelection} from "./serializerMatrix";

// NOTE: the Pyodide pipeline is imported lazily (inside ensureConverted), not
// at module top. It pulls a `new Worker(new URL(...))` that Vite emits as a
// separate chunk, which must NOT land in the single-file embed bundle that
// paradoc consumes. Keeping it behind a dynamic import keeps pyodide_converter
// out of any static graph that never runs an in-browser conversion (the embed).

export type {TargetFormat} from "@/services/viewerApi";

function shouldUsePyodide(sourceKey: string, targetFormat: TargetFormat): boolean {
    // The in-browser engine is the default for every (source, target) it
    // supports; the support matrix lives in wasmSupport.ts so the pipeline,
    // this router, and the audit-run sweep agree. The server worker takes a
    // supported conversion only when the user opted into it AND an
    // administrator granted them worker access (workerPreferred). Unsupported
    // combos have no browser path and go to the server worker for everyone.
    if (!wasmSupportsConversion(sourceKey, targetFormat)) return false;
    return !workerPreferred();
}

/** STEP/IFC -> GLB in the browser through adacpp's native (embind, no Pyodide) CAD->GLB
 * modules: the same reader and writer the server uses, so the GLB carries the same scene
 * hierarchy, and no Python runtime has to boot. Returns the derived key, or null when no
 * native module covers the pair or it failed -- the caller then uses the Pyodide pipeline. */
async function convertNativeFirst(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat,
): Promise<string | null> {
    const {convertViaWasmNativeAndUpload, nativeCadGlbSupported} = await import("./nativeCadGlbPipeline");
    if (!nativeCadGlbSupported(sourceKey, targetFormat)) return null;
    try {
        return await convertViaWasmNativeAndUpload(scope, sourceKey, targetFormat);
    } catch (err) {
        console.warn(`native ${extOf(sourceKey)} -> ${targetFormat} failed; using the Pyodide pipeline`, err);
        return null;
    }
}

/** True when a supported in-browser conversion goes to the worker because the
 * user chose it: the submission the server's worker-access policy judges. */
function choseWorker(sourceKey: string, targetFormat: TargetFormat): boolean {
    return wasmSupportsConversion(sourceKey, targetFormat) && workerPreferred();
}

/**
 * Enqueue a conversion for a source key in a given scope and resolve
 * when the derived blob is ready. Returns the derived storage key.
 *
 * Routes through the Pyodide in-browser path (the default) whenever the
 * source/target combination is supported there and the user has not opted
 * into the server worker (see workerPreferred); otherwise hits the
 * server-side NATS pipeline, which requires worker access. Throws on API
 * rejection (403 without worker access), job error, or poll-timeout.
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
            "use_sat_pcurves" | "skip_shapefix" | "merge_meshes"
            | "profile_conversions" | "step_streamer",
            boolean | null
        >>;
    },
): Promise<string> {
    if (shouldUsePyodide(sourceKey, targetFormat)) {
        // A forced streaming-STEP request is a worker-side engine knob; honour it there.
        if (!opts?.conversionOptions?.step_streamer) {
            const native = await convertNativeFirst(scope, sourceKey, targetFormat);
            if (native !== null) return native;
        }
        const {convertViaPyodideAndUpload} = await import("./pyodidePipeline");
        return convertViaPyodideAndUpload(scope, sourceKey, targetFormat);
    }
    if (!runtime.convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }
    return convertViaServer(scope, sourceKey, targetFormat, {
        ...opts,
        runOnWorker: choseWorker(sourceKey, targetFormat),
    });
}

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

/**
 * Convert routing the serializer/tessellator selection (from the reconvert
 * dropdowns) to the right pipeline: a client serializer (runtime="client",
 * i.e. the in-browser WASM/Pyodide path) goes to the Pyodide pipeline, any
 * server serializer forwards ``{serializer, tessellator}`` as conversion
 * options to the worker where :func:`_apply_glb_serializer` folds them into the
 * engine knobs. Returns the derived storage key. Shared by the gallery
 * reconvert tool and the /convert page so both honour the same matrix.
 */
export async function convertWithSelection(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat,
    opts?: {
        selection?: SerializerSelection;
        extraOptions?: Record<string, boolean | string | number | null>;
        reconvert?: boolean;
    },
): Promise<string> {
    const ext = extOf(sourceKey);
    const schema = serializerSchemaFor(ext, targetFormat);
    const selection = opts?.selection ?? {};
    const resolved = schema ? normalizeSelection(schema, selection) : null;

    if (resolved?.isClient) {
        // In-browser path — no server round-trip. Two client engines, picked by
        // the tessellator token:
        //   wasm-native → the OCC-free, no-pyodide adacpp_step_glb embind module
        //                 (STEP → GLB only, 2.3 MB, no Python runtime).
        //   pyodide     → the Pyodide pipeline (ifc / step / mesh, wasm wheels).
        if (resolved.tessellator === "wasm-native") {
            // CAD→GLB (step/ifc → glb) via the native embind GLB modules. The default for an
            // untouched row (normalizeSelection): then a native failure falls back to Pyodide;
            // an explicit "wasm-native" pick reports it.
            const untouched = !selection.tessellator;
            if (untouched) {
                const native = await convertNativeFirst(scope, sourceKey, targetFormat);
                if (native !== null) return native;
            }
            const {convertViaWasmNativeAndUpload, nativeCadGlbSupported} = await import(
                "./nativeCadGlbPipeline"
            );
            if (!untouched && nativeCadGlbSupported(sourceKey, targetFormat)) {
                return convertViaWasmNativeAndUpload(scope, sourceKey, targetFormat);
            }
            // B-rep→B-rep (step→ifc / ifc→step) via the native embind writer module.
            const {convertViaWasmBrepAndUpload, nativeBrepWriterSupported} = await import(
                "./nativeBrepWriterPipeline"
            );
            if (nativeBrepWriterSupported(sourceKey, targetFormat)) {
                return convertViaWasmBrepAndUpload(scope, sourceKey, targetFormat);
            }
            // No native module covers this (source, target) — fall through to Pyodide.
        }
        if (!wasmSupportsConversion(sourceKey, targetFormat)) {
            throw new Error(
                `in-browser conversion doesn't support ${ext || sourceKey} → ${targetFormat}`,
            );
        }
        const {convertViaPyodideAndUpload} = await import("./pyodidePipeline");
        return convertViaPyodideAndUpload(scope, sourceKey, targetFormat);
    }

    // A pair that offers no serializer to choose (an FEA deck to another deck) has nothing on
    // the row to say where it runs, so the user's global choice decides, exactly as it does for
    // every other conversion (`ensureConverted`). Without this the converter page sent them to a
    // worker even with "Convert in-browser" on.
    if (!schema && shouldUsePyodide(sourceKey, targetFormat)) {
        const native = await convertNativeFirst(scope, sourceKey, targetFormat);
        if (native !== null) return native;
        const {convertViaPyodideAndUpload} = await import("./pyodidePipeline");
        return convertViaPyodideAndUpload(scope, sourceKey, targetFormat);
    }

    if (!runtime.convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }
    const conversionOptions: Record<string, boolean | string | number | null> = {
        ...(opts?.extraOptions ?? {}),
    };
    if (resolved) {
        conversionOptions.serializer = resolved.serializer;
        conversionOptions.tessellator = resolved.tessellator;
    }
    return convertViaServer(scope, sourceKey, targetFormat, {
        conversionOptions: Object.keys(conversionOptions).length ? conversionOptions : undefined,
        reconvert: opts?.reconvert,
        // A server serializer picked from the row is a serializer choice, not a
        // choice of the worker over the browser; only the global engine setting is.
        runOnWorker: !resolved && choseWorker(sourceKey, targetFormat),
    });
}

// Backwards-compatible wrapper for the GLB-for-viewing flow. ``opts.streamer``
// forces the memory-bounded streaming STEP->GLB converter (for large assemblies
// that OOM the normal OCC path).
export async function ensureConvertedGlb(
    scope: ScopeUrl,
    sourceKey: string,
    opts?: {streamer?: boolean},
): Promise<void> {
    await ensureConverted(
        scope,
        sourceKey,
        "glb",
        opts?.streamer ? {conversionOptions: {step_streamer: true}} : undefined,
    );
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
    // In-browser (WASM) FEA bake by default for every source the browser can
    // bake (incl. already-converted Abaqus .sqlite); the server worker bakes it
    // only on the user's opt-in with worker access, or when the browser can't
    // (native .odb export needs Abaqus — server only, 'abaqus' capability).
    if (isWasmFeaSource(sourceKey) && !workerPreferred()) {
        const {convertViaPyodideFeaBake} = await import("./pyodidePipeline");
        await convertViaPyodideFeaBake(scope, sourceKey);
        return;
    }
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
            // A browser-bakeable source reaches here only on the user's opt-in.
            runOnWorker: isWasmFeaSource(sourceKey),
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
