// Public surface of the conversion subsystem. Routes a conversion or an FEA bake
// to adacpp's native wasm modules, the in-browser Pyodide pipeline or the server
// worker pool, in the order the execution policy gives (services/compute/
// executionPolicy.ts), falling back to the next engine when one fails.

import {useConversionStore} from "@/state/conversionStore";
import {runtime} from "@/runtime/config";
import {viewerApi, TargetFormat, ScopeUrl} from "@/services/viewerApi";
import type {FeaManifest, ResultMeta} from "@/services/viewerApi";
import {resultMetaFromManifest} from "@/services/feaManifestPoll";
import {
    allowedEngines,
    executionAudit,
    planExecution,
    runWithFallback,
    type AttemptContext,
    type EngineRunners,
    type ExecEngine,
    type ExecutionPlan,
} from "@/services/compute/executionPolicy";
import {sourceQualifier} from "@/services/compute/executionPolicyModel";
import {refreshAccessIfStale} from "@/services/compute/workerAccess";
import {convertViaServer} from "./serverPipeline";
import {wasmSupportsConversion, isWasmFeaSource, nativeSupportsConversion} from "./wasmSupport";
import {serializerSchemaFor, normalizeSelection, SerializerSelection} from "./serializerMatrix";

// NOTE: the Pyodide and native pipelines are imported lazily (inside the runners),
// not at module top. The Pyodide one pulls a `new Worker(new URL(...))` that Vite
// emits as a separate chunk, which must NOT land in the single-file embed bundle
// that paradoc consumes. Keeping them behind a dynamic import keeps them out of
// any static graph that never runs an in-browser conversion (the embed).

export type {TargetFormat} from "@/services/viewerApi";

type ConversionOperation = "convert" | "read";

/** Which engines can handle (source, target) at all. ``nativeOff``: a forced
 * streaming-STEP request is a worker-side engine knob, so the native module is
 * not offered for it. ``browserOff``: the request asks for something no browser
 * engine does (a FEA result's picked step and field), so neither is offered. */
function conversionSupport(sourceKey: string, targetFormat: TargetFormat, nativeOff = false, browserOff = false) {
    return (engine: ExecEngine): boolean =>
        engine === "native"
            ? !browserOff && !nativeOff && nativeSupportsConversion(sourceKey, targetFormat)
            : engine === "pyodide"
              ? !browserOff && wasmSupportsConversion(sourceKey, targetFormat)
              : true;
}

/** The plan for one conversion: the policy's order for (op, source extension). */
export function conversionPlan(
    op: ConversionOperation,
    sourceKey: string,
    targetFormat: TargetFormat,
    opts?: {nativeOff?: boolean; browserOff?: boolean},
): ExecutionPlan {
    return planExecution(
        op,
        sourceQualifier(sourceKey),
        conversionSupport(sourceKey, targetFormat, opts?.nativeOff, opts?.browserOff),
    );
}

/**
 * ``plan`` for a run the user set worker-side options for: a server serializer, or the
 * engine knobs of the admin storage reconvert. The browser engines would ignore those, so
 * where a browser path exists the run is a choice of the worker over the browser: the worker
 * alone, sent as ``X-Ada-Run-On: worker`` and judged by the server. Refused here, saying why,
 * when the policy does not allow this user the worker. With no browser path the plan is the
 * worker already (unjudged) and is returned as it is.
 */
export function workerOptInPlan(plan: ExecutionPlan, what: string): ExecutionPlan {
    if (!plan.browserPath) return plan;
    if (!allowedEngines(plan.op, plan.qualifier).includes("worker")) {
        throw new Error(
            `${what} needs the server worker, which the execution policy does not allow you for ${plan.op} (${plan.qualifier}). Ask an administrator to change it in the Admin Panel (Execution).`,
        );
    }
    return {...plan, engines: ["worker"]};
}

async function runNative(scope: ScopeUrl, sourceKey: string, targetFormat: TargetFormat, ctx: AttemptContext) {
    const {convertViaWasmNativeAndUpload} = await import("./nativeCadGlbPipeline");
    return convertViaWasmNativeAndUpload(scope, sourceKey, targetFormat, {execution: executionAudit(ctx)});
}

async function runPyodide(scope: ScopeUrl, sourceKey: string, targetFormat: TargetFormat, ctx: AttemptContext) {
    const {convertViaPyodideAndUpload} = await import("./pyodidePipeline");
    return convertViaPyodideAndUpload(scope, sourceKey, targetFormat, {execution: executionAudit(ctx)});
}

function requireServerConversion(): void {
    if (!runtime.convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }
}

/**
 * Convert a source key in a given scope and resolve when the derived blob is
 * ready. Returns the derived storage key.
 *
 * The engines come from the execution policy for ``opts.operation`` ("read", the
 * default: a source opened for viewing; "convert": an explicit conversion) and the
 * source's extension, skipping the ones that cannot handle the pair and falling
 * back to the next one on a failure. A conversion no browser engine supports goes
 * to the server worker for everyone (unjudged); one the browser could run goes
 * there only when the policy allows it and is sent as an explicit worker choice.
 * Throws when every allowed engine failed (a 403 from the server included) or the
 * policy allows none.
 *
 * ``opts.step``/``opts.field`` are forwarded to the server for FEA result picks. No
 * browser engine renders a picked step and field (they render the default one), so a
 * pick goes to the worker as the only path.
 *
 * ``opts.workerOptIn``: the caller set worker-side options (``conversionOptions`` the
 * browser engines ignore); the run is then an explicit worker choice (workerOptInPlan).
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
        operation?: ConversionOperation;
        workerOptIn?: boolean;
    },
): Promise<string> {
    // Route by the policy as it is now: an administrator may have changed it since the page loaded.
    await refreshAccessIfStale();
    const operation = opts?.operation ?? "read";
    let plan = conversionPlan(operation, sourceKey, targetFormat, {
        nativeOff: Boolean(opts?.conversionOptions?.step_streamer),
        browserOff: opts?.step != null || opts?.field != null,
    });
    if (opts?.workerOptIn) plan = workerOptInPlan(plan, "The conversion options you set");
    const {operation: _op, workerOptIn: _optIn, ...serverOpts} = opts ?? {};
    void _op;
    void _optIn;
    const {value} = await runWithFallback(
        plan,
        {
            native: (ctx) => runNative(scope, sourceKey, targetFormat, ctx),
            pyodide: (ctx) => runPyodide(scope, sourceKey, targetFormat, ctx),
            worker: (ctx) => {
                requireServerConversion();
                return convertViaServer(scope, sourceKey, targetFormat, {
                    ...serverOpts,
                    runOnWorker: ctx.explicitWorker,
                    operation,
                });
            },
        },
        {label: `${operation} ${sourceKey} -> ${targetFormat}`},
    );
    return value;
}

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

/**
 * Convert routing the serializer/tessellator selection (from the reconvert
 * dropdowns) to the right pipeline. Shared by the gallery reconvert tool and the
 * /convert page so both honour the same matrix. Returns the derived storage key.
 *
 * An UNTOUCHED row (no serializer or tessellator picked) follows the execution
 * policy for "convert" like every other conversion (native, Pyodide, worker in the
 * policy's order, falling back on a failure); the worker then gets the row's
 * server default serializer. A row the user set is that user's per-conversion
 * choice and runs as picked (a client serializer must be an engine the policy
 * allows them). A server serializer picked there runs on the worker, and where a
 * browser engine could have converted the pair that is a choice of the worker over
 * the browser: sent as one (``X-Ada-Run-On: worker``) and judged, see workerOptInPlan.
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
    await refreshAccessIfStale();
    const ext = extOf(sourceKey);
    const schema = serializerSchemaFor(ext, targetFormat);
    const selection = opts?.selection ?? {};
    const touched = Boolean(selection.serializer || selection.tessellator);

    const serverConvert = (
        resolved: {serializer: string; tessellator: string} | null,
        runOnWorker: boolean,
    ): Promise<string> => {
        requireServerConversion();
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
            runOnWorker,
            operation: "convert",
        });
    };

    if (schema && touched) {
        const resolved = normalizeSelection(schema, selection, ext);
        if (!resolved.isClient) {
            const plan = workerOptInPlan(
                conversionPlan("convert", sourceKey, targetFormat),
                `The ${resolved.serializer} serializer`,
            );
            return serverConvert(resolved, plan.browserPath);
        }
        if (!selection.tessellator) {
            // A client serializer with no tessellator picked: the browser engines in
            // the policy's order, falling back between them (not to the worker: the
            // user asked for the browser).
            const plan = conversionPlan("convert", sourceKey, targetFormat);
            const browserOnly = {...plan, engines: plan.engines.filter((e) => e !== "worker")};
            const {value} = await runWithFallback(
                browserOnly,
                {
                    native: (ctx) => runNative(scope, sourceKey, targetFormat, ctx),
                    pyodide: (ctx) => runPyodide(scope, sourceKey, targetFormat, ctx),
                },
                {label: `convert ${sourceKey} -> ${targetFormat}`},
            );
            return value;
        }
        // In-browser path, picked on the row. Two client engines, by tessellator token:
        //   wasm-native -> adacpp's native embind modules (CAD->GLB, or the B-rep writer)
        //   pyodide     -> the Pyodide pipeline (ifc / step / mesh, wasm wheels).
        // The literal is a cross-language contract with adapy's wasm serializer tokens
        // (test_converter_step_routing.py pins it): keep it spelled out here.
        const engine: ExecEngine = resolved.tessellator === "wasm-native" ? "native" : "pyodide";
        const allowed = allowedEngines("convert", sourceQualifier(sourceKey));
        if (!allowed.includes(engine)) {
            const list = allowed.join(", ") || "none";
            throw new Error(
                `The execution policy does not allow the ${engine} engine for converting ${ext || sourceKey} (allowed: ${list}). Ask an administrator to change it in the Admin Panel (Execution).`,
            );
        }
        if (engine === "native") {
            const {convertViaWasmNativeAndUpload, nativeCadGlbSupported} = await import("./nativeCadGlbPipeline");
            if (nativeCadGlbSupported(sourceKey, targetFormat)) {
                return convertViaWasmNativeAndUpload(scope, sourceKey, targetFormat);
            }
            // B-rep->B-rep (step->ifc / ifc->step) via the native embind writer module.
            const {convertViaWasmBrepAndUpload, nativeBrepWriterSupported} = await import("./nativeBrepWriterPipeline");
            if (nativeBrepWriterSupported(sourceKey, targetFormat)) {
                return convertViaWasmBrepAndUpload(scope, sourceKey, targetFormat);
            }
            // No native module covers this (source, target) -- fall through to Pyodide.
        }
        if (!wasmSupportsConversion(sourceKey, targetFormat)) {
            throw new Error(`in-browser conversion doesn't support ${ext || sourceKey} → ${targetFormat}`);
        }
        const {convertViaPyodideAndUpload} = await import("./pyodidePipeline");
        return convertViaPyodideAndUpload(scope, sourceKey, targetFormat);
    }

    // Untouched (or a pair with no serializer to choose): the policy decides.
    const plan = conversionPlan("convert", sourceKey, targetFormat);
    const serverDefault = schema
        ? normalizeSelection(schema, {serializer: String(schema.serializer.default ?? "")}, ext)
        : null;
    const runners: EngineRunners<string> = {
        native: (ctx) => runNative(scope, sourceKey, targetFormat, ctx),
        pyodide: (ctx) => runPyodide(scope, sourceKey, targetFormat, ctx),
        worker: (ctx) =>
            serverConvert(serverDefault && !serverDefault.isClient ? serverDefault : null, ctx.explicitWorker),
    };
    const {value} = await runWithFallback(plan, runners, {label: `convert ${sourceKey} -> ${targetFormat}`});
    return value;
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

/** The plan for an FEA bake of ``sourceKey``: Pyodide where the browser can bake
 * it, the worker as the policy orders. */
export function feaBakePlan(sourceKey: string): ExecutionPlan {
    return planExecution("fea_bake", sourceQualifier(sourceKey), (engine) =>
        engine === "pyodide" ? isWasmFeaSource(sourceKey) : engine === "worker",
    );
}

/**
 * Eagerly bake the streaming-FEA manifest for a source file.
 *
 * Symmetric to ``ensureConvertedGlb`` but targets the /fea/manifest
 * pipeline used by streaming-only formats (.rmed, .odb, ...) — those
 * don't go through the legacy single-GLB path. Engines in the execution
 * policy's "fea_bake" order (in-browser Pyodide for what the browser can
 * bake, the worker after it; native .odb export is server only).
 *
 * Store key follows the ``${sourceKey}::${target}`` convention from
 * serverPipeline (target ``fea`` here) so a manifest bake and a GLB
 * conversion of the same source can coexist without colliding.
 */
export async function ensureBakedFeaManifest(
    scope: ScopeUrl,
    sourceKey: string,
): Promise<void> {
    await refreshAccessIfStale();
    await runWithFallback(
        feaBakePlan(sourceKey),
        {
            pyodide: async (ctx) => {
                const {convertViaPyodideFeaBake} = await import("./pyodidePipeline");
                await convertViaPyodideFeaBake(scope, sourceKey, {execution: executionAudit(ctx)});
            },
            worker: (ctx) => bakeOnWorker(scope, sourceKey, ctx.explicitWorker),
        },
        {label: `fea_bake ${sourceKey}`},
    );
}

/**
 * A FEA result file's (steps, fields) inventory, the step/field picker's list.
 *
 * Engines in the "fea_bake" order, like the bake it comes from: where the browser bakes the
 * source, the inventory is read from the bake's manifest (baked here first when it is not
 * stored yet), so no worker parses the deck; the worker's ``/result-meta`` after it, as the
 * policy orders (an explicit choice when the browser could have baked it).
 */
export async function resolveResultMeta(scope: ScopeUrl, sourceKey: string): Promise<ResultMeta> {
    await refreshAccessIfStale();
    const {value} = await runWithFallback(
        feaBakePlan(sourceKey),
        {
            pyodide: async (ctx) => resultMetaFromManifest(await browserBakedManifest(scope, sourceKey, ctx)),
            worker: (ctx) => viewerApi.resultMeta(scope, sourceKey, {runOnWorker: ctx.explicitWorker}),
        },
        {label: `result_meta ${sourceKey}`},
    );
    return value;
}

/** The stored bake manifest of ``sourceKey`` (``_derived/<source>.fea/fea.manifest.json``,
 * where both bakes write it), baked in the browser first when there is none. */
async function browserBakedManifest(scope: ScopeUrl, sourceKey: string, ctx: AttemptContext): Promise<FeaManifest> {
    const key = `_derived/${sourceKey.replace(/^\/+|\/+$/g, "")}.fea/fea.manifest.json`;
    const read = async () => JSON.parse(new TextDecoder().decode(await viewerApi.getBlob(scope, key))) as FeaManifest;
    try {
        return await read();
    } catch {
        // Not stored yet: bake it.
    }
    const {convertViaPyodideFeaBake} = await import("./pyodidePipeline");
    await convertViaPyodideFeaBake(scope, sourceKey, {execution: executionAudit(ctx)});
    return read();
}

async function bakeOnWorker(scope: ScopeUrl, sourceKey: string, runOnWorker: boolean): Promise<void> {
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
            runOnWorker,
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
