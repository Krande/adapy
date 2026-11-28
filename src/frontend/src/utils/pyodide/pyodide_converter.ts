// Lazy launcher + RPC wrapper for the Pyodide conversion worker.
//
// The worker is heavy (~10MB Pyodide runtime + ~30MB IFC wheel), so we
// spawn it on first use only — never on page load. Subsequent calls
// reuse the warm worker. Each request gets a unique reqId so
// concurrent calls don't cross-talk.

import {runtime} from "@/runtime/config";
import {
    bakedPyodidePackages,
    bakedRuntimeWheels,
    loadPluginWheelsManifest,
    patchPyodideLock,
    planPluginInstall,
    planPluginsInstall,
    pluginsForCapability,
    PLUGIN_WHEELS_DIR,
    PYODIDE_VERSION,
    resolvePyodideSource,
    type PluginInstallPlan,
} from "./pluginWheels";

let workerPromise: Promise<Worker> | null = null;
let nextReqId = 1;
// Plugins whose install the live worker was already asked to prewarm.
let prewarmedPlugins = new Set<string>();
// Peak wasm linear-memory the live worker last reported. Pyodide never frees
// heap back to the OS, so once a worker has grown near the wasm32 ceiling we
// recycle it (terminate + respawn) before the NEXT job rather than risk an OOM
// abort mid-conversion.
let lastHeapBytes = 0;

// A single in-browser conversion that runs longer than this is treated as hung
// (or thrashing against the memory limit) and its worker is killed.
const JOB_TIMEOUT_MS = 300_000;
// Recycle the worker once its heap crosses this (~1.4 GB) — comfortably under
// the ~2 GB emscripten/wasm32 ceiling, leaving room for one more conversion's
// working set before a fresh worker is spawned.
const RECYCLE_HEAP_BYTES = 1_400_000_000;

export interface LocalArtefactOutput {
    key: string;
    bytes: Uint8Array;
    contentType: string;
    contentEncoding?: string | null;
}

export interface PluginJobResult {
    summary: Record<string, unknown>;
    localArtefacts: LocalArtefactOutput[];
    heap?: number;
}

type WorkerMessage =
    | {type: "log"; message: string}
    | {type: "ready"}
    | {type: "progress"; reqId: number; message: string; fraction: number}
    | {type: "result"; reqId: number; bytes: Uint8Array; heap?: number}
    | {
          type: "plugin-result";
          reqId: number;
          summary: Record<string, unknown>;
          localArtefacts: LocalArtefactOutput[];
          heap?: number;
      }
    | {
          type: "core-result";
          reqId: number;
          status: string;
          outputs: Array<{name: string; bytes: Uint8Array}>;
          heap?: number;
      }
    | {type: "error"; reqId?: number; message: string};

/** Terminate the live worker (the "subprocess") and reset state so the next
 * call spawns a fresh one. Never touches the page/main thread. */
function killWorker(): void {
    const p = workerPromise;
    workerPromise = null;
    lastHeapBytes = 0;
    prewarmedPlugins = new Set();
    if (p) p.then((w) => w.terminate()).catch(() => {});
}

/** What the worker boots with: where Pyodide loads from and, when the build baked
 * Pyodide packages next to the plugin wheels, a lock file pointing at them. */
export interface PyodideBootConfig {
    version: string;
    indexURL: string;
    lockFileContents?: unknown;
    /** Baked wheels for the worker's pure-Python stacks (trimesh, pyquaternion), by name. */
    runtimeWheels?: Record<string, {file: string; sha256?: string}>;
}

// The page's URL, to resolve origin-relative Pyodide and wheel URLs against.
function pageUrl(): string {
    return (globalThis as {location?: {href?: string}}).location?.href ?? "http://localhost/";
}

async function resolveBootConfig(onLog?: (msg: string) => void): Promise<PyodideBootConfig> {
    const here = pageUrl();
    const manifest = await loadPluginWheelsManifest();
    const source = resolvePyodideSource(runtime.pyodideIndexUrl(), manifest, here, (m) => onLog?.(m));
    const config: PyodideBootConfig = {version: PYODIDE_VERSION, indexURL: source.indexURL};
    const runtimeWheels = bakedRuntimeWheels(manifest);
    if (Object.keys(runtimeWheels).length > 0) config.runtimeWheels = runtimeWheels;
    const baked = bakedPyodidePackages(manifest);
    if (baked.length === 0) return config;
    try {
        const resp = await fetch(`${source.indexURL}pyodide-lock.json`);
        if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
        const patch = patchPyodideLock(await resp.json(), baked, new URL(PLUGIN_WHEELS_DIR, here).href);
        for (const s of patch.skipped) onLog?.(`baked ${s.name} not used: ${s.reason}`);
        if (patch.redirected.length > 0) config.lockFileContents = patch.lock;
    } catch (err) {
        // The packages then load from the index by name, as without baking.
        onLog?.(`Pyodide lock file not read (${err instanceof Error ? err.message : String(err)}); baked packages unused`);
    }
    return config;
}

function spawnWorker(onLog?: (msg: string) => void): Promise<Worker> {
    return new Promise((resolve, reject) => {
        // new URL(..., import.meta.url) lets Vite bundle the worker
        // file as an asset and rewrite the URL at build time.
        const worker = new Worker(
            new URL("./pyodide_worker.js", import.meta.url),
        );
        const onMessage = (e: MessageEvent<WorkerMessage>) => {
            const data = e.data;
            if (data.type === "log") {
                onLog?.(data.message);
            } else if (data.type === "ready") {
                worker.removeEventListener("message", onMessage);
                resolve(worker);
            } else if (data.type === "error" && data.reqId === undefined) {
                // Pre-ready bootstrap error — the worker won't recover.
                worker.removeEventListener("message", onMessage);
                worker.terminate();
                workerPromise = null;
                reject(new Error(data.message));
            }
        };
        worker.addEventListener("message", onMessage);
        worker.addEventListener("error", (e) => {
            workerPromise = null;
            reject(new Error(e.message || "worker spawn error"));
        });
        // Boot is implicit on the first message, which must carry the boot config
        // (the worker cannot load Pyodide before it knows where from). Every
        // other message waits for "ready", so this one is always first.
        resolveBootConfig(onLog)
            .catch((): PyodideBootConfig => {
                const source = resolvePyodideSource(runtime.pyodideIndexUrl(), {plugins: {}}, pageUrl());
                return {version: PYODIDE_VERSION, indexURL: source.indexURL};
            })
            .then((pyodide) => worker.postMessage({type: "boot", pyodide}));
    });
}

/** The install plans for one plugin, or none when the manifest does not list it. */
async function pluginPlans(pluginId: string): Promise<PluginInstallPlan[] | null> {
    const manifest = await loadPluginWheelsManifest();
    if (!manifest.plugins[pluginId]) return null;
    return planPluginInstall(manifest, pluginId);
}

export function isPyodideWorkerReady(): boolean {
    return workerPromise !== null;
}

export async function ensurePyodideWorker(onLog?: (msg: string) => void): Promise<Worker> {
    if (!workerPromise) {
        workerPromise = spawnWorker(onLog);
    }
    return workerPromise;
}

/** Boot the worker and pre-load the CAD stack in the background. Call when the
 * WASM engine is enabled so the first conversion doesn't pay the cold
 * pyodide-boot + kernel-load cost when the user opens a file. Fire-and-forget.
 *
 * `plugins`: manifest plugin ids to install too, after the core stack, one at a
 * time (their dependency files included). Each id is sent once per worker; a job
 * for a plugin still installing waits for that same install. */
export function prewarmPyodide(onLog?: (msg: string) => void, opts?: {plugins?: readonly string[]}): void {
    void (async () => {
        let ids: string[] = [];
        let plans: PluginInstallPlan[] = [];
        if (opts?.plugins) {
            // A plugins-only call with nothing new to install does not boot the worker.
            const manifest = await loadPluginWheelsManifest();
            ids = opts.plugins.filter((id) => !prewarmedPlugins.has(id) && id in manifest.plugins);
            if (ids.length === 0) return;
            plans = planPluginsInstall(manifest, ids);
        }
        const w = await ensurePyodideWorker(onLog);
        for (const id of ids) prewarmedPlugins.add(id);
        w.postMessage({type: "prewarm", plugins: plans});
    })().catch((err) => {
        onLog?.(`prewarm not started: ${err instanceof Error ? err.message : String(err)}`);
        /* best-effort warm-up; a real conversion will surface any error */
    });
}

export type PyodideSourceFormat = "ifc" | "step" | "mesh" | "sat" | "fea" | "fea_glb" | "fem" | "genie";

/** Run a single conversion via the Pyodide worker. Format selects which
 * pyodide stack handles the bytes — ifc → ifcopenshell+trimesh; step →
 * adacpp.cad (OCCT-cross-compiled wasm); mesh → trimesh (the ``ext``
 * tells trimesh which loader to use: obj/stl/ply/gltf/dae/off); sat →
 * adapy ACIS parser + adacpp backend (returns GLB); fea → adapy FEA bake
 * (returns a zip of the streaming-viewer artefact tree, ``ext`` =
 * rmed/med/sif/sin); fea_glb → SIF/SIN result → single tessellated GLB
 * (FEAResult.to_gltf, the registry's lone target for those sources). */
// Shared result/error/timeout plumbing for one worker job. Attaches its
// listeners synchronously (so they're live before the caller posts), and
// resolves with the result bytes / rejects on handled error, crash, or
// timeout. Caller posts the request message after calling this.
function awaitWorkerResult(worker: Worker, reqId: number, onLog?: (msg: string) => void): Promise<Uint8Array> {
    return new Promise<Uint8Array>((resolve, reject) => {
        let settled = false;
        const cleanup = () => {
            worker.removeEventListener("message", onMessage);
            worker.removeEventListener("error", onError);
            clearTimeout(timer);
        };
        // kill=true tears down the worker (used when it's crashed/hung/OOM);
        // a handled conversion error keeps the warm worker for the next job.
        const fail = (message: string, kill: boolean) => {
            if (settled) return;
            settled = true;
            cleanup();
            if (kill) killWorker();
            reject(new Error(message));
        };
        const onMessage = (e: MessageEvent<WorkerMessage>) => {
            const data = e.data;
            if (data.type === "log") {
                onLog?.(data.message);
                return;
            }
            if ((data.type === "result" || data.type === "error") && data.reqId !== reqId) {
                return;
            }
            if (settled) return;
            if (data.type === "result") {
                settled = true;
                cleanup();
                if (typeof data.heap === "number") lastHeapBytes = data.heap;
                resolve(data.bytes);
            } else if (data.type === "error") {
                // Handled Python-side failure (incl. MemoryError) — worker is
                // still healthy, keep it warm.
                fail(data.message, false);
            }
        };
        // The worker thread itself died — a fatal wasm abort/OOM. Kill + respawn;
        // the page is unaffected (this is the "kill the subprocess, not the
        // page" guarantee).
        const onError = () => fail("wasm worker crashed (likely out-of-memory); it was restarted", true);
        // Hung or thrashing against the memory limit: terminate the worker so it
        // can't wedge the engine; the next conversion gets a fresh one.
        const timer = setTimeout(
            () => fail(`wasm conversion exceeded ${JOB_TIMEOUT_MS / 1000}s; worker terminated`, true),
            JOB_TIMEOUT_MS,
        );
        worker.addEventListener("message", onMessage);
        worker.addEventListener("error", onError);
    });
}

export async function convertViaPyodide(
    format: PyodideSourceFormat,
    bytes: ArrayBuffer,
    /** ``srcKey``: the source's storage key; an FEA bake records it as its manifest's ``src``. */
    opts?: {onLog?: (msg: string) => void; ext?: string; target?: string; srcKey?: string},
): Promise<Uint8Array> {
    // Proactively recycle a worker whose heap has grown near the ceiling, so
    // this job starts with reclaimed memory instead of risking an OOM abort.
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const worker = await ensurePyodideWorker(opts?.onLog);
    const reqId = nextReqId++;
    const result = awaitWorkerResult(worker, reqId, opts?.onLog);
    worker.postMessage(
        {type: "convert", reqId, format, ext: opts?.ext, target: opts?.target ?? "glb", srcKey: opts?.srcKey, bytes},
        [bytes],
    );
    return result;
}

/** Compile a procedural cell-model document to GLB bytes entirely in-browser via
 * the built-in adapy engine (ada.topo_model.wasm_compile.compile_doc). `doc` is
 * the cellbuilder commit document; it is JSON-stringified and handed to the
 * warm Pyodide worker. No server round-trip. */
/** A kind:wheel engine descriptor: micropip-install the wheel (+ deps) then
 * dispatch to its module:callable entrypoint. */
export interface PyodideEngineWheel {
    entrypoint: string;
    deps: string[];
    url: string;
}

export async function compileProceduralViaPyodide(
    doc: unknown,
    opts?: {
        onLog?: (msg: string) => void;
        engine?: string | null;
        wheel?: PyodideEngineWheel | null;
    },
): Promise<Uint8Array> {
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const worker = await ensurePyodideWorker(opts?.onLog);
    const reqId = nextReqId++;
    const result = awaitWorkerResult(worker, reqId, opts?.onLog);
    // engine = a built-in slug (echo) dispatched directly; wheel = an external
    // engine the worker micropip-installs, then dispatches via its entrypoint.
    const engine = opts?.engine && opts.engine !== "adapy-default" ? opts.engine : null;
    const wheel = opts?.wheel ?? null;
    worker.postMessage({type: "procedural-compile", reqId, doc: JSON.stringify(doc), engine, wheel});
    return result;
}

/** Stream a conversion from a range-capable URL instead of uploading the whole
 * source — for sources too large to stage in wasm memory (a multi-GB SIN
 * exceeds the wasm32 ceiling and the 2 GiB ArrayBuffer cap). The worker reads
 * the source on demand via synchronous-XHR Range requests against `url` (a
 * presigned / Range-capable URL), so only the touched pages cross into wasm.
 * Currently supports `fea_glb` (Sesam `.sin` → single-step GLB). */
export async function convertViaPyodideStream(
    format: PyodideSourceFormat,
    url: string,
    opts?: {onLog?: (msg: string) => void; ext?: string; target?: string; size?: number; headers?: Record<string, string>},
): Promise<Uint8Array> {
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const worker = await ensurePyodideWorker(opts?.onLog);
    const reqId = nextReqId++;
    const result = awaitWorkerResult(worker, reqId, opts?.onLog);
    worker.postMessage({
        type: "convert",
        reqId,
        stream: true,
        url,
        size: opts?.size,
        headers: opts?.headers,
        format,
        ext: opts?.ext,
        target: opts?.target ?? "glb",
    });
    return result;
}

/** Stream a FEA bake from a range-capable source URL AND have the worker ship
 * each baked artefact file straight to the server (`uploadUrl`, one POST per
 * file) as it lands — so neither the multi-GB source nor the whole output tree
 * (nor a zip of it) is ever held in wasm memory. Resolves to the bake summary
 * (`{count, files, manifest}`); the artefacts are already uploaded under
 * `_derived/<source>.fea/`. `uploadUrl` must already carry `?source=…`. */
export async function convertViaPyodideFeaBakeStream(
    url: string,
    uploadUrl: string,
    opts?: {
        onLog?: (msg: string) => void;
        ext?: string;
        size?: number;
        headers?: Record<string, string>;
        uploadHeaders?: Record<string, string>;
        /** The source's storage key, recorded as the manifest's ``src``. */
        srcKey?: string;
    },
): Promise<{count: number; files: string[]; manifest: string; bytes: number}> {
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const worker = await ensurePyodideWorker(opts?.onLog);
    const reqId = nextReqId++;
    const result = awaitWorkerResult(worker, reqId, opts?.onLog);
    worker.postMessage({
        type: "convert",
        reqId,
        stream: true,
        url,
        uploadUrl,
        uploadHeaders: opts?.uploadHeaders,
        size: opts?.size,
        headers: opts?.headers,
        format: "fea",
        ext: opts?.ext,
        target: "glb",
        srcKey: opts?.srcKey,
    });
    const bytes = await result;
    return JSON.parse(new TextDecoder().decode(bytes));
}

/** Run a plugin job_entrypoint in the browser Pyodide worker against
 * BrowserStorageFacade. Outputs are returned as in-memory Uint8Arrays for local
 * OPFS/memory persistence and optional explicit Publish to Scope. */
export async function runPluginJobViaPyodide(opts: {
    pluginId: string;
    scope: string;
    options: Record<string, unknown>;
    derivedPrefix?: string;
    entrypoint?: string;
    apiBase?: string;
    authHeaders?: Record<string, string>;
    onLog?: (msg: string) => void;
    onProgress?: (message: string, fraction: number) => void;
}): Promise<PluginJobResult> {
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const plans = await pluginPlans(opts.pluginId);
    if (!plans) throw new Error(`No browser wheel registered in plugin-wheels-manifest.json for "${opts.pluginId}"`);
    const worker = await ensurePyodideWorker(opts.onLog);
    const reqId = nextReqId++;
    const resultPromise = new Promise<PluginJobResult>((resolve, reject) => {
        let settled = false;
        const cleanup = () => {
            worker.removeEventListener("message", onMessage);
            worker.removeEventListener("error", onError);
            clearTimeout(timer);
        };
        const fail = (message: string, kill: boolean) => {
            if (settled) return;
            settled = true;
            cleanup();
            if (kill) killWorker();
            reject(new Error(message));
        };
        const onMessage = (e: MessageEvent<WorkerMessage>) => {
            const data = e.data;
            if (data.type === "log") {
                opts.onLog?.(data.message);
                return;
            }
            if (data.type === "progress" && data.reqId === reqId) {
                opts.onProgress?.(data.message, data.fraction);
                return;
            }
            if ((data.type === "plugin-result" || data.type === "error") && data.reqId !== reqId) {
                return;
            }
            if (settled) return;
            if (data.type === "plugin-result") {
                settled = true;
                cleanup();
                if (typeof data.heap === "number") lastHeapBytes = data.heap;
                resolve({
                    summary: data.summary,
                    localArtefacts: data.localArtefacts,
                    heap: data.heap,
                });
            } else if (data.type === "error") {
                fail(data.message, false);
            }
        };
        const onError = () => fail("wasm worker crashed (likely out-of-memory); it was restarted", true);
        const timer = setTimeout(
            () => fail(`wasm plugin job exceeded ${JOB_TIMEOUT_MS / 1000}s; worker terminated`, true),
            JOB_TIMEOUT_MS,
        );
        worker.addEventListener("message", onMessage);
        worker.addEventListener("error", onError);
    });
    worker.postMessage({
        type: "run-plugin-job",
        reqId,
        pluginId: opts.pluginId,
        plans,
        scope: opts.scope,
        options: opts.options,
        derivedPrefix: opts.derivedPrefix ?? "",
        entrypoint: opts.entrypoint,
        apiBase: opts.apiBase ?? "/api",
        authHeaders: opts.authHeaders,
    });
    return resultPromise;
}

/** A claimed core job (services/compute/coreJobs.ts) as the worker gets it. */
export interface CoreJobClaimMessage {
    id: string;
    kind: string;
    source_key: string;
    derived_key: string;
    conversion_options: Record<string, unknown>;
    target_capability: string | null;
    scope: string;
}

/** Run a claimed core job (a clash check, a detail, a component build, a selection export, an
 * equipment bbox, a scene utility) in the browser Pyodide worker: ada.comms.rest.wasm_jobs.run,
 * the worker handler's own work, reading the scope's blobs through a synchronous facade. The
 * plugin wheels that serve the job's pool (manifest `capabilities`) are installed first, so a
 * checker or spec they register is there. Resolves to the run's status and its outputs by name. */
export async function runCoreJobViaPyodide(
    claim: CoreJobClaimMessage,
    opts: {
        apiBase?: string;
        authHeaders?: Record<string, string>;
        onLog?: (msg: string) => void;
        onProgress?: (stage: string, fraction: number) => void;
    } = {},
): Promise<{status: "done" | "error"; error: string | null; traceback?: string | null; outputs: Record<string, Uint8Array>}> {
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const manifest = await loadPluginWheelsManifest();
    const plugins = pluginsForCapability(manifest, claim.target_capability);
    const plans = plugins.length ? planPluginsInstall(manifest, plugins) : [];
    const worker = await ensurePyodideWorker(opts.onLog);
    const reqId = nextReqId++;
    const result = new Promise<{status: string; outputs: Array<{name: string; bytes: Uint8Array}>}>((resolve, reject) => {
        let settled = false;
        const cleanup = () => {
            worker.removeEventListener("message", onMessage);
            worker.removeEventListener("error", onError);
            clearTimeout(timer);
        };
        const fail = (message: string, kill: boolean) => {
            if (settled) return;
            settled = true;
            cleanup();
            if (kill) killWorker();
            reject(new Error(message));
        };
        const onMessage = (e: MessageEvent<WorkerMessage>) => {
            const data = e.data;
            if (data.type === "log") {
                opts.onLog?.(data.message);
                return;
            }
            if (data.type === "progress" && data.reqId === reqId) {
                opts.onProgress?.(data.message, data.fraction);
                return;
            }
            if ((data.type === "core-result" || data.type === "error") && data.reqId !== reqId) return;
            if (settled) return;
            if (data.type === "core-result") {
                settled = true;
                cleanup();
                if (typeof data.heap === "number") lastHeapBytes = data.heap;
                resolve({status: data.status, outputs: data.outputs});
            } else if (data.type === "error") {
                fail(data.message, false);
            }
        };
        const onError = () => fail("wasm worker crashed (likely out-of-memory); it was restarted", true);
        const timer = setTimeout(
            () => fail(`wasm core job exceeded ${JOB_TIMEOUT_MS / 1000}s; worker terminated`, true),
            JOB_TIMEOUT_MS,
        );
        worker.addEventListener("message", onMessage);
        worker.addEventListener("error", onError);
    });
    worker.postMessage({
        type: "run-core-job",
        reqId,
        claim: JSON.stringify(claim),
        plans,
        apiBase: opts.apiBase ?? "/api",
        authHeaders: opts.authHeaders,
    });
    const {status, outputs} = await result;
    const doc = JSON.parse(status) as {status: "done" | "error"; error: string | null; traceback?: string | null};
    return {...doc, outputs: Object.fromEntries(outputs.map((o) => [o.name, o.bytes]))};
}

/** Run a procedural engine registered in /wheels/plugin-wheels-manifest.json
 * (a baked plugin wheel keyed by the engine's slug, whose register hook announces
 * the engine) in the browser Pyodide worker. Dispatches by slug, as the worker does. */
export async function runProceduralEngineViaPyodide(
    engineId: string,
    doc: unknown,
    opts?: {onLog?: (msg: string) => void; lod?: "sim" | "detail"},
): Promise<Uint8Array> {
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const plans = await pluginPlans(engineId);
    const worker = await ensurePyodideWorker(opts?.onLog);
    const reqId = nextReqId++;
    const result = awaitWorkerResult(worker, reqId, opts?.onLog);
    worker.postMessage({
        type: "run-procedural-engine",
        reqId,
        engineId,
        plans,
        doc: JSON.stringify(doc),
        lod: opts?.lod ?? "sim",
    });
    return result;
}

/** What `runProceduralIoViaPyodide` runs (ada.topo_model.wasm_io.run's actions). */
export type ProceduralIoAction = "export_xlsx" | "import_xlsx" | "propose_relocations" | "export_model";

/** A procedural model's Excel export / import, relocation proposals or IFC/Genie
 * export in the browser Pyodide worker (ada.topo_model.wasm_io.run), the same engine
 * calls the server's jobs make. `engineId` names an engine a baked plugin wheel
 * registers (its install plans are installed first, as for a compile); null is a
 * built-in engine. Resolves to the file bytes (export_xlsx / export_model) or the
 * UTF-8 JSON answer (import_xlsx: the document; propose_relocations: the proposals). */
export async function runProceduralIoViaPyodide(
    action: ProceduralIoAction,
    payload: Record<string, unknown>,
    opts?: {onLog?: (msg: string) => void; engineId?: string | null; data?: Uint8Array | null},
): Promise<Uint8Array> {
    if (workerPromise && lastHeapBytes > RECYCLE_HEAP_BYTES) {
        killWorker();
    }
    const engineId = opts?.engineId ?? null;
    const plans = engineId ? await pluginPlans(engineId) : null;
    if (engineId && !plans) throw new Error(`No browser wheel registered in plugin-wheels-manifest.json for "${engineId}"`);
    const worker = await ensurePyodideWorker(opts?.onLog);
    const reqId = nextReqId++;
    const result = awaitWorkerResult(worker, reqId, opts?.onLog);
    const data = opts?.data ?? null;
    worker.postMessage(
        {type: "run-procedural-io", reqId, action, payload: JSON.stringify(payload), engineId, plans, data},
        data ? [data.buffer] : [],
    );
    return result;
}

/** Backwards-compatible IFC entry point — keeps existing callers working. */
export async function convertIfcViaPyodide(
    bytes: ArrayBuffer,
    opts?: {onLog?: (msg: string) => void},
): Promise<Uint8Array> {
    return convertViaPyodide("ifc", bytes, opts);
}

/** Tear down the worker. Useful for hot-reload during development. */
export function shutdownPyodideWorker(): void {
    if (!workerPromise) return;
    workerPromise.then((w) => w.terminate()).catch(() => {});
    workerPromise = null;
}
