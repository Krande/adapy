// Web Worker: the browser FEA engine. Owns the adacpp_fea wasm module and
// EVERY byte of the OPFS store (services/fea/opfsFeaStore.ts).
//
// Single owner, on purpose: OPFS sync access handles are exclusive, so the
// store's files are only ever opened here (opfsNativeFeaFs), and every read
// the viewer makes of the store comes here too (``readFile``). The kernel
// never touches OPFS: its inputs are staged into the module's in-heap FS
// (feaEngineCore.ts) -- WASMFS's OPFS backend traps in this single-threaded
// build.
//
// Without OPFS (no sync access handles, a private window) the store and its
// index live in memory: cases still compute, they just do not outlive the page.
//
// Base strides are fetched through the CALLER's range fetcher (a Comlink proxy
// of the main thread's ``makeViewerApiFetcher``), so auth and the API base stay
// where they are configured.

import * as Comlink from "comlink";

import type {FeaEnvelope, FeaManifest} from "@/services/api/fea";
import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";

import {FeaEngineCore, type FeaKernelModule, type MaterialisedCase} from "@/services/fea/feaEngineCore";
import type {FeaRangeFetcher} from "@/services/fea/feaFetcher";
import {
    dexieFeaIndex,
    FEA_STORE_ROOT,
    memoryFeaFs,
    memoryFeaIndex,
    OpfsFeaStore,
    opfsNativeFeaFs,
    type FeaStoreUsage,
    type OpfsDirHandleLike,
} from "@/services/fea/opfsFeaStore";

function hasSyncAccessHandles(): boolean {
    return typeof (globalThis as {FileSystemSyncAccessHandle?: unknown}).FileSystemSyncAccessHandle !== "undefined";
}

/** Where the module is served (``public/wasm`` in dev, ``/wasm`` in the image). */
export const FEA_WASM_URL = "/wasm/adacpp_fea.js";

export interface FeaEngineProbe {
    ok: boolean;
    error?: string;
    version?: string;
    /** The store is on OPFS (persistent); false = in-heap, page-lifetime. */
    opfs: boolean;
    syncAccessHandle: boolean;
    simd: boolean;
}

// The wasm-feature-detect SIMD probe: a module using one v128 instruction.
const SIMD_PROBE = new Uint8Array([
    0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11,
]);

function simdSupported(): boolean {
    try {
        return typeof WebAssembly !== "undefined" && WebAssembly.validate(SIMD_PROBE);
    } catch {
        return false;
    }
}

interface Engine {
    core: FeaEngineCore;
    opfs: boolean;
}

let enginePromise: Promise<Engine> | null = null;

function engine(): Promise<Engine> {
    if (!enginePromise) {
        enginePromise = (async () => {
            const mod = await loadEmscriptenModule<FeaKernelModule>(FEA_WASM_URL);
            const storage = (globalThis as {navigator?: Navigator}).navigator?.storage;
            // OPFS through its own API (sync access handles: worker only). The
            // module's WASMFS OPFS mount is NOT used: in the single-threaded
            // build every file operation on it traps (see feaEngineCore.ts).
            let root: OpfsDirHandleLike | null = null;
            if (hasSyncAccessHandles() && storage?.getDirectory) {
                root = (await storage.getDirectory().catch(() => null)) as unknown as OpfsDirHandleLike | null;
            }
            const opfs = root !== null;
            const store = new OpfsFeaStore({
                fs: opfs ? opfsNativeFeaFs(root!) : memoryFeaFs(),
                index: opfs ? await dexieFeaIndex().catch(() => memoryFeaIndex()) : memoryFeaIndex(),
                root: FEA_STORE_ROOT,
                estimate: storage?.estimate ? () => storage.estimate() : undefined,
                persist: opfs && storage?.persist ? () => storage.persist() : undefined,
            });
            return {core: new FeaEngineCore(mod, store), opfs};
        })();
        enginePromise.catch(() => {
            enginePromise = null;
        });
    }
    return enginePromise;
}

const api = {
    /** Can this browser run the engine? Loads the module (once). */
    async probe(): Promise<FeaEngineProbe> {
        const base = {syncAccessHandle: hasSyncAccessHandles(), simd: simdSupported()};
        try {
            const e = await engine();
            return {ok: true, version: e.core.version(), opfs: e.opfs, ...base};
        } catch (err) {
            return {ok: false, error: err instanceof Error ? err.message : String(err), opfs: false, ...base};
        }
    },

    /** Warm the store with the base strides ``fields`` need (default: every
     *  case field) for ``caseNs`` (default: every Tier-A combination). */
    async ensureBase(
        sourceId: string,
        manifest: FeaManifest,
        fetcher: FeaRangeFetcher,
        opts: {fields?: string[]; caseNs?: number[]} = {},
    ): Promise<number> {
        const {core} = await engine();
        return core.ensureBase(sourceId, manifest, fetcher, opts);
    },

    /** Materialise (or read back) combination ``caseN``: the overlay, same
     *  shape as the server's ``fea.case.json`` + ``prefix``. Rejects with a
     *  ``LocalCaseUnsupported`` message for a recipe only the server can do. */
    async materialiseCase(
        sourceId: string,
        sourceKey: string,
        manifest: FeaManifest,
        caseN: number,
        fetcher: FeaRangeFetcher,
    ): Promise<MaterialisedCase> {
        const {core} = await engine();
        return core.materialiseCase({sourceId, sourceKey, manifest, caseN, fetch: fetcher});
    },

    async envelope(sourceId: string, manifest: FeaManifest, field: string, fetcher: FeaRangeFetcher): Promise<FeaEnvelope> {
        const {core} = await engine();
        return core.envelope({sourceId, manifest, field, fetch: fetcher});
    },

    /** Bytes of ``rel`` under the source's tree (``cases/<d>/<f>``, ``base/<f>``,
     *  ``envelopes/<dir>/<f>``), whole or ``[start, end]``; null when absent. */
    async readFile(
        sourceId: string,
        bakeVersion: number,
        rel: string,
        range?: {start: number; end: number},
    ): Promise<ArrayBuffer | null> {
        const {core} = await engine();
        const bytes = await core.readFile(sourceId, bakeVersion, rel, range);
        if (!bytes) return null;
        const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
        return Comlink.transfer(buf, [buf]);
    },

    async usage(): Promise<FeaStoreUsage> {
        const {core} = await engine();
        return core.store.usage();
    },

    /** Evict down to ``targetBytes`` (default: the budget). Returns the ids evicted. */
    async evict(targetBytes?: number): Promise<string[]> {
        const {core} = await engine();
        return core.store.evict(targetBytes === undefined ? {} : {targetBytes});
    },

    async clear(sourceId?: string): Promise<void> {
        const {core} = await engine();
        await core.store.clear(sourceId);
        core.dropMirrors();
    },

    async setPinned(sourceId: string, pinned: boolean): Promise<void> {
        const {core} = await engine();
        await core.store.setPinned(sourceId, pinned);
    },
};

export type FeaEngineWorkerAPI = typeof api;
Comlink.expose(api);
