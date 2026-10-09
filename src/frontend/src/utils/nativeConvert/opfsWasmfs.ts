// Shared OPFS-via-WASMFS streaming helpers for the native (no-pyodide) adacpp embind modules
// (cadGlbConverter, brepWriterConverter). All IO goes THROUGH WASMFS — never the browser OPFS API —
// so a file streamed in is immediately visible to the C++ `pread` (WASMFS caches its dir listing at
// mount time, so a browser-OPFS-created file can be invisible to the module). Writing through the
// OPFS-mounted path still lands each chunk on disk (bounded RSS), so multi-GB sources never have to
// fit the wasm heap.

// The WASMFS mount point (maps to the OPFS root). Each module has its OWN FS, so mounting the same
// point in different modules is independent.
export const OPFS_MOUNT = "/opfs";

// Minimal surface the helpers need — every adacpp embind module built with -sWASMFS + EXPORTED FS
// satisfies this structurally.
export interface WasmfsModule {
    FS: {
        readFile(path: string): Uint8Array;
        unlink(path: string): void;
        mkdir(path: string): void;
        open(path: string, flags: string): unknown;
        write(stream: unknown, buffer: Uint8Array, offset: number, length: number, position: number): number;
        close(stream: unknown): void;
    };
    /** The OPFS mount of an adacpp release with the sync-access-handle backend: resolves once the
     *  mount is usable, rejects with the reason when the browser cannot back it. */
    opfsMount?(mountPoint: string, opts?: {scratch?: number}): Promise<unknown>;
    /** The older mount. In single-threaded builds its WASMFS OPFS backend needs pthreads or JSPI: it
     *  returned 0 and then every file operation on the mount trapped. Never trusted -- see below. */
    mountOpfs?(mountPoint: string): number;
}

// wasmfs_create_opfs_backend() ABORTS the whole module (fatal) if called on the main browser thread
// without JSPI — which would also kill the buffered fallback that shares the module. It is only safe
// inside a dedicated Worker (emscripten_is_main_browser_thread() == false there). Callers always run
// in a Worker, but guard anyway so a mount can never abort the shared module.
function inDedicatedWorker(): boolean {
    return (
        typeof (globalThis as {WorkerGlobalScope?: unknown}).WorkerGlobalScope !== "undefined" &&
        typeof (globalThis as {DedicatedWorkerGlobalScope?: unknown}).DedicatedWorkerGlobalScope !== "undefined" &&
        (globalThis as unknown as {self?: unknown}).self instanceof
            (globalThis as unknown as {DedicatedWorkerGlobalScope: new () => unknown}).DedicatedWorkerGlobalScope
    );
}

// Per-module "mounted" latch (modules are distinct objects; WeakSet keeps this GC-friendly).
const mounted = new WeakSet<object>();

/** Mount OPFS at OPFS_MOUNT for this module (once). Resolves false when not in a worker, when the
 * module has no working OPFS backend, or when the browser cannot back it -- the caller then falls back
 * to the buffered MEMFS path.
 *
 * Only `opfsMount` (adacpp's sync-access-handle backend) is used. A module with just the older
 * `mountOpfs` is treated as having no OPFS: that backend reported success and then trapped on the
 * first file operation in every browser, so trusting it turned every large conversion into a failure
 * where the buffered path would have worked. */
export async function ensureOpfsMounted(Module: WasmfsModule): Promise<boolean> {
    if (mounted.has(Module)) return true;
    if (!inDedicatedWorker()) return false;
    if (typeof Module.opfsMount !== "function") return false;
    try {
        await Module.opfsMount(OPFS_MOUNT);
        mounted.add(Module);
        return true;
    } catch (err) {
        // eslint-disable-next-line no-console
        console.info("[native-convert] OPFS streaming unavailable, using the in-memory path:", err);
        return false;
    }
}

/** Write `bytes` to `path`, replacing any file already there. WASMFS 4.0.9's `FS.writeFile` APPENDS to
 * an existing file (fixed in newer adacpp modules), so a leftover input from a failed run would be
 * prepended to the next one; deleting first makes every module version write a fresh file. */
export function writeFileFresh(Module: Pick<WasmfsModule, "FS"> & {FS: {writeFile(path: string, data: Uint8Array): void}}, path: string, bytes: Uint8Array): void {
    try {
        Module.FS.unlink(path);
    } catch {
        /* nothing there */
    }
    Module.FS.writeFile(path, bytes);
}

/** Stream `sourceUrl` into `inPath` (under OPFS_MOUNT) THROUGH WASMFS, chunk by chunk. Each chunk
 * writes straight through to OPFS at its offset and is freed; nothing accumulates in the heap, and the
 * file is immediately visible to the module's `pread`. */
export async function streamUrlToOpfs(Module: WasmfsModule, inPath: string, sourceUrl: string): Promise<void> {
    const resp = await fetch(sourceUrl);
    if (!resp.ok || !resp.body) {
        throw new Error(`fetch source failed: ${resp.status} ${resp.statusText}`);
    }
    const stream = Module.FS.open(inPath, "w");
    try {
        const reader = resp.body.getReader();
        let pos = 0;
        for (;;) {
            const {done, value} = await reader.read();
            if (done) break;
            Module.FS.write(stream, value, 0, value.byteLength, pos);
            pos += value.byteLength;
        }
    } finally {
        Module.FS.close(stream);
    }
}

/** Best-effort FS.unlink of every path (ignore missing). */
export function unlinkAll(Module: WasmfsModule, paths: string[]): void {
    for (const p of paths) {
        try {
            Module.FS.unlink(p);
        } catch {
            /* best-effort cleanup */
        }
    }
}
