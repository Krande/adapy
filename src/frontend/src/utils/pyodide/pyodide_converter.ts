// Lazy launcher + RPC wrapper for the Pyodide conversion worker.
//
// The worker is heavy (~10MB Pyodide runtime + ~30MB IFC wheel), so we
// spawn it on first use only — never on page load. Subsequent calls
// reuse the warm worker. Each request gets a unique reqId so
// concurrent calls don't cross-talk.

let workerPromise: Promise<Worker> | null = null;
let nextReqId = 1;

type WorkerMessage =
    | {type: "log"; message: string}
    | {type: "ready"}
    | {type: "result"; reqId: number; bytes: Uint8Array}
    | {type: "error"; reqId?: number; message: string};

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

export type PyodideSourceFormat = "ifc" | "step" | "mesh" | "sat" | "fea";

/** Run a single conversion via the Pyodide worker. Format selects which
 * pyodide stack handles the bytes — ifc → ifcopenshell+trimesh; step →
 * adacpp.cad (OCCT-cross-compiled wasm); mesh → trimesh (the ``ext``
 * tells trimesh which loader to use: obj/stl/ply/gltf/dae/off); sat →
 * adapy ACIS parser + adacpp backend (returns GLB); fea → adapy FEA bake
 * (returns a zip of the streaming-viewer artefact tree, ``ext`` =
 * rmed/med/sif/sin). */
export async function convertViaPyodide(
    format: PyodideSourceFormat,
    bytes: ArrayBuffer,
    opts?: {onLog?: (msg: string) => void; ext?: string; target?: string},
): Promise<Uint8Array> {
    const worker = await ensurePyodideWorker(opts?.onLog);
    const reqId = nextReqId++;
    return new Promise<Uint8Array>((resolve, reject) => {
        const onMessage = (e: MessageEvent<WorkerMessage>) => {
            const data = e.data;
            if (data.type === "log") {
                opts?.onLog?.(data.message);
                return;
            }
            if ((data.type === "result" || data.type === "error") && data.reqId !== reqId) {
                return;
            }
            worker.removeEventListener("message", onMessage);
            if (data.type === "result") {
                resolve(data.bytes);
            } else if (data.type === "error") {
                reject(new Error(data.message));
            }
        };
        worker.addEventListener("message", onMessage);
        worker.postMessage(
            {type: "convert", reqId, format, ext: opts?.ext, target: opts?.target ?? "glb", bytes},
            [bytes],
        );
    });
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
