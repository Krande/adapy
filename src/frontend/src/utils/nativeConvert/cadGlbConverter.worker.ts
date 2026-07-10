// Web Worker: CAD (STEP / IFC) -> GLB entirely in the browser via the OCC-free, NO-pyodide adacpp
// embind wasm modules (native Part-21 / IfcResolver reader + libtess2 + meshoptimizer + GLB writer).
// The lightweight counterpart to the pyodide pipeline — ~2.3 MB per module, no Python runtime.
//
// embind surface (see adacpp/src/cad/{cad_wasm,ifc_glb_wasm}.cpp):
//   stepToGlb / ifcToGlb(inPath, outPath, spillDir, deflection, angularDeg, meshopt) -> product count
//                                                                                        (<0 on error)
//   mountOpfs(mountPoint) -> 0 ok
// IO goes through the emscripten FS. Buffered path uses in-heap MEMFS; the OPFS-streaming path mounts
// OPFS via WASMFS so a multi-GB source reads off-disk via `pread` (bounded RSS), never the wasm heap.

import * as Comlink from "comlink";

import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";

// Which native module + verb + source extension a `kind` maps to. Each module ships its own single
// verb; both share the FS + mountOpfs surface.
export type CadKind = "step" | "ifc";
const MODULES: Record<CadKind, {url: string; verb: string; ext: string}> = {
    step: {url: "/wasm/adacpp_step_glb.js", verb: "stepToGlb", ext: "step"},
    ifc: {url: "/wasm/adacpp_ifc_glb.js", verb: "ifcToGlb", ext: "ifc"},
};

interface EmscriptenFS {
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    mkdir(path: string): void;
    unlink(path: string): void;
}
interface EmModule {
    FS: EmscriptenFS;
    mountOpfs(mountPoint: string): number;
    // The per-module conversion verb (stepToGlb / ifcToGlb) — same signature, looked up by name.
    [verb: string]: unknown;
}
type ConvertVerb = (
    inPath: string,
    outPath: string,
    spillDir: string,
    deflection: number,
    angularDeg: number,
    meshopt: boolean,
) => number;

const modulePromises: Partial<Record<CadKind, Promise<EmModule>>> = {};
function getModule(kind: CadKind): Promise<EmModule> {
    let p = modulePromises[kind];
    if (!p) {
        p = loadEmscriptenModule<EmModule>(MODULES[kind].url);
        modulePromises[kind] = p;
    }
    return p;
}
function convertVerb(Module: EmModule, kind: CadKind): ConvertVerb {
    return Module[MODULES[kind].verb] as ConvertVerb;
}

export interface NativeCadGlbResult {
    glb: ArrayBuffer;
    products: number; // products (STEP solids / IFC products) written, not triangles
    ms: number;
}

// OPFS mount + streaming file names. The mount maps to the OPFS root, so a browser-written OPFS file
// at root name `N` is visible to WASMFS at `${OPFS_MOUNT}/N` (emscripten OPFS backend interop). Names
// are namespaced so the two modules (each with its own FS) never collide with unrelated OPFS data.
const OPFS_MOUNT = "/opfs";
const opfsInName = (kind: CadKind) => `adacpp_${kind}glb_in.${MODULES[kind].ext}`;
const opfsOutPath = (kind: CadKind) => `${OPFS_MOUNT}/adacpp_${kind}glb_out.glb`;
const opfsSpillDir = (kind: CadKind) => `${OPFS_MOUNT}/adacpp_${kind}glb_spill`;

// wasmfs_create_opfs_backend() ABORTS the whole module (fatal) if called on the main browser thread
// without JSPI — which would also kill the buffered fallback that shares this module. It is only safe
// inside a dedicated Worker (emscripten_is_main_browser_thread() == false there). This file always
// runs in a Worker (instantiated via `new Worker`), but guard it anyway so the mount can never abort
// the shared module.
function inDedicatedWorker(): boolean {
    return (
        typeof (globalThis as {WorkerGlobalScope?: unknown}).WorkerGlobalScope !== "undefined" &&
        typeof (globalThis as {DedicatedWorkerGlobalScope?: unknown}).DedicatedWorkerGlobalScope !== "undefined" &&
        (globalThis as unknown as {self?: unknown}).self instanceof
            (globalThis as unknown as {DedicatedWorkerGlobalScope: new () => unknown}).DedicatedWorkerGlobalScope
    );
}

const opfsMounted: Partial<Record<CadKind, boolean>> = {};
function ensureOpfsMounted(Module: EmModule, kind: CadKind): boolean {
    if (opfsMounted[kind]) return true;
    if (!inDedicatedWorker()) return false; // never risk the fatal main-thread mount assertion
    try {
        if (Module.mountOpfs(OPFS_MOUNT) === 0) {
            opfsMounted[kind] = true;
            return true;
        }
    } catch {
        /* OPFS unavailable — caller falls back to the buffered MEMFS path */
    }
    return false;
}

// The worker-only OPFS sync-access-handle API (createSyncAccessHandle) isn't in every TS DOM lib —
// probe it structurally via `unknown`.
function syncHandleSupported(): boolean {
    const g = globalThis as unknown as {
        FileSystemFileHandle?: {prototype?: Record<string, unknown>};
        navigator?: {storage?: {getDirectory?: unknown}};
    };
    const proto = g.FileSystemFileHandle?.prototype;
    return (
        typeof proto?.createSyncAccessHandle === "function" &&
        typeof g.navigator?.storage?.getDirectory === "function"
    );
}

// Stream `sourceUrl` into an OPFS file via a worker-only sync access handle, chunk by chunk, so
// neither the JS heap nor the wasm heap ever holds the whole source. Returns the OPFS-mount path
// WASMFS reads it from.
async function streamUrlToOpfs(sourceUrl: string, kind: CadKind): Promise<string> {
    const nav = globalThis.navigator as unknown as {storage: {getDirectory(): Promise<any>}};
    const root = await nav.storage.getDirectory();
    const fh = await root.getFileHandle(opfsInName(kind), {create: true});
    const access = await fh.createSyncAccessHandle();
    try {
        access.truncate(0);
        const resp = await fetch(sourceUrl);
        if (!resp.ok || !resp.body) {
            throw new Error(`fetch source failed: ${resp.status} ${resp.statusText}`);
        }
        const reader = resp.body.getReader();
        let at = 0;
        for (;;) {
            const {done, value} = await reader.read();
            if (done) break;
            // write() copies `value` straight to the OPFS-backed file at `at`; nothing accumulates.
            access.write(value, {at});
            at += value.byteLength;
        }
        access.flush();
    } finally {
        access.close();
    }
    return `${OPFS_MOUNT}/${opfsInName(kind)}`;
}

async function removeOpfsInput(kind: CadKind): Promise<void> {
    try {
        const nav = globalThis.navigator as unknown as {storage: {getDirectory(): Promise<any>}};
        const root = await nav.storage.getDirectory();
        await root.removeEntry(opfsInName(kind));
    } catch {
        /* best-effort cleanup */
    }
}

const api = {
    // Can this worker run the OPFS-streaming tier for `kind`? (feature-detect only; the pipeline
    // decides when to use it — large sources with a presigned URL.)
    async opfsAvailable(kind: CadKind): Promise<boolean> {
        if (!syncHandleSupported()) return false;
        return ensureOpfsMounted(await getModule(kind), kind);
    },

    // OPFS-streaming path: stream a (presigned) URL into OPFS, tessellate off-disk via pread, write
    // the GLB to OPFS, read it back through WASMFS. For multi-GB sources that can't fit the wasm heap.
    async toGlbStreaming(
        kind: CadKind,
        sourceUrl: string,
        opts: {deflection: number; angularDeg: number; meshopt: boolean},
    ): Promise<NativeCadGlbResult> {
        const Module = await getModule(kind);
        if (!syncHandleSupported() || !ensureOpfsMounted(Module, kind)) {
            throw new Error("OPFS streaming unavailable in this worker (no sync access handles)");
        }
        const t0 = performance.now();
        const inPath = await streamUrlToOpfs(sourceUrl, kind);
        const outPath = opfsOutPath(kind);
        try {
            Module.FS.mkdir(opfsSpillDir(kind));
        } catch {
            /* already exists on a reused module */
        }
        const products = convertVerb(Module, kind)(
            inPath,
            outPath,
            opfsSpillDir(kind),
            opts.deflection,
            opts.angularDeg,
            opts.meshopt,
        );
        if (products < 0) {
            await removeOpfsInput(kind);
            throw new Error(`native streaming ${kind.toUpperCase()}→GLB failed (I/O error in the wasm module)`);
        }
        // Read the OPFS-written GLB back through WASMFS (consistent within WASMFS; no cross-handle
        // flush race). This materialises the output once — the GLB is far smaller than the source.
        const out = Module.FS.readFile(outPath);
        const glb = out.slice().buffer;
        try {
            Module.FS.unlink(outPath);
        } catch {
            /* best-effort */
        }
        await removeOpfsInput(kind);
        const result: NativeCadGlbResult = {glb, products, ms: performance.now() - t0};
        return Comlink.transfer(result, [glb]);
    },

    // Buffered path: source bytes -> MEMFS (in-heap) -> GLB. Simplest; fine below the OPFS threshold.
    async toGlb(
        kind: CadKind,
        srcBytes: ArrayBuffer,
        opts: {deflection: number; angularDeg: number; meshopt: boolean},
    ): Promise<NativeCadGlbResult> {
        const Module = await getModule(kind);
        const inPath = `/in.${MODULES[kind].ext}`;
        const outPath = "/out.glb";
        const spillDir = "/spill";
        const t0 = performance.now();
        Module.FS.writeFile(inPath, new Uint8Array(srcBytes));
        try {
            Module.FS.mkdir(spillDir);
        } catch {
            /* already exists on a reused module — fine */
        }
        const products = convertVerb(Module, kind)(
            inPath,
            outPath,
            spillDir,
            opts.deflection,
            opts.angularDeg,
            opts.meshopt,
        );
        if (products < 0) {
            throw new Error(`native ${kind.toUpperCase()}→GLB failed (I/O error in the wasm module)`);
        }
        const out = Module.FS.readFile(outPath);
        // Own a transferable copy off the wasm heap, then release the FS entries so a reused module
        // doesn't accumulate files across conversions.
        const glb = out.slice().buffer;
        try {
            Module.FS.unlink(inPath);
            Module.FS.unlink(outPath);
        } catch {
            /* best-effort cleanup */
        }
        const result: NativeCadGlbResult = {glb, products, ms: performance.now() - t0};
        return Comlink.transfer(result, [glb]);
    },
};

export type CadGlbConverterAPI = typeof api;
Comlink.expose(api);
