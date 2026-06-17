// Lazy launcher + RPC wrapper for the Pyodide conversion worker.
//
// The worker is heavy (~10MB Pyodide runtime + ~30MB IFC wheel), so we
// spawn it on first use only — never on page load. Subsequent calls
// reuse the warm worker. Each request gets a unique reqId so
// concurrent calls don't cross-talk.

let workerPromise: Promise<Worker> | null = null;
let nextReqId = 1;
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

type WorkerMessage =
    | {type: "log"; message: string}
    | {type: "ready"}
    | {type: "result"; reqId: number; bytes: Uint8Array; heap?: number}
    | {type: "error"; reqId?: number; message: string};

/** Terminate the live worker (the "subprocess") and reset state so the next
 * call spawns a fresh one. Never touches the page/main thread. */
function killWorker(): void {
    const p = workerPromise;
    workerPromise = null;
    lastHeapBytes = 0;
    if (p) p.then((w) => w.terminate()).catch(() => {});
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
        // Boot is implicit on first message. Send a ping-style message
        // to kick it off; the worker treats any message as the boot
        // trigger.
        worker.postMessage({type: "boot"});
    });
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
 * pyodide-boot + kernel-load cost when the user opens a file. Fire-and-forget. */
export function prewarmPyodide(onLog?: (msg: string) => void): void {
    ensurePyodideWorker(onLog)
        .then((w) => w.postMessage({type: "prewarm"}))
        .catch(() => {
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
    opts?: {onLog?: (msg: string) => void; ext?: string; target?: string},
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
        {type: "convert", reqId, format, ext: opts?.ext, target: opts?.target ?? "glb", bytes},
        [bytes],
    );
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
