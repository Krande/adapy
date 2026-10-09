// Web Worker: ONE shard of a STEP / IFC -> GLB conversion split across several workers (see
// cadGlbShardPool.ts for the coordinator). Each worker loads its own instance of the native adacpp
// module and does its share through the module's `glbShards` helper; the workers share nothing but an
// OPFS job directory, so this needs no SharedArrayBuffer and no cross-origin isolation.
//
// Module surface (adacpp src/wasmio/glb_shards.js, src/cad/{step,ifc}_glb_shard.h):
//   glbShards.prepare(job, input, nworkers)             scan once, write <job>/index
//   glbShards.open(job, input, lane, {deflection, angular}) -> {roots, huge, batches(n)}
//   glbShards.processHuge(h, f0, f1) / assembleHuge(h, chunk)
//   glbShards.process(begin, end)
//   glbShards.persist()                                  lane -> <job>/lanes
//   glbShards.merge(nworkers, meshopt)                   <job>/lanes -> <job>/out.glb

import * as Comlink from "comlink";

import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";
import {OPFS_MOUNT, WasmfsModule, ensureOpfsMounted} from "./opfsWasmfs";
import type {CadKind} from "./cadGlbConverter.worker";

const MODULE_URL: Record<CadKind, string> = {
    step: "/wasm/adacpp_step_glb.js",
    ifc: "/wasm/adacpp_ifc_glb.js",
};

export interface ShardOpenInfo {
    /** Ordinary roots (huge ones excluded), or -1 when the index could not be read. */
    roots: number;
    /** Face count of each huge root (split by face range across the workers). */
    huge: number[];
    /** Exclusive end of each batch of ordinary roots, about equal in estimated cost. */
    ends: number[];
}

interface GlbShards {
    prepare(job: string, input: string, nworkers: number): Promise<number>;
    open(
        job: string,
        input: string,
        lane: number,
        opts: {deflection: number; angular: number},
    ): Promise<{roots: number; huge: number[]; batches(n: number): number[]}>;
    process(begin: number, end: number): number;
    processHuge(h: number, f0: number, f1: number): Promise<number>;
    assembleHuge(h: number, chunk: number): Promise<number>;
    persist(): Promise<boolean>;
    merge(nworkers: number, meshopt: boolean): Promise<number>;
}

interface ShardModule extends WasmfsModule {
    glbShards?: GlbShards;
    opfsOpen(path: string, opts?: {create?: boolean; readOnly?: boolean}): Promise<void>;
    opfsDetach(path: string): Promise<void>;
}

let Module: ShardModule | null = null;
let shards: GlbShards | null = null;

function need(): GlbShards {
    if (!shards) throw new Error("shard worker not initialised");
    return shards;
}

const api = {
    /** Answer once the worker runs: the coordinator's spawn handshake. */
    ping(): true {
        return true;
    },

    /** Load the module and mount OPFS. Resolves false when this module or browser cannot shard (no
     *  `glbShards` in this adacpp release, or no OPFS sync access handles). */
    async init(kind: CadKind): Promise<boolean> {
        Module = await loadEmscriptenModule<ShardModule>(MODULE_URL[kind]);
        if (!Module.glbShards || !(await ensureOpfsMounted(Module))) return false;
        shards = Module.glbShards;
        return true;
    },

    /** Can several workers read one OPFS file at once? Sharding needs shared read-only sync access
     *  handles (`mode: "read-only"`); without them the second worker to open the input is refused. A
     *  two-handle probe on a tiny file answers that before any source is staged. */
    async canShareReads(): Promise<boolean> {
        const root = await navigator.storage.getDirectory();
        const dir = await root.getDirectoryHandle("adacpp-shards", {create: true});
        const fh = await dir.getFileHandle("probe", {create: true});
        type ReadOnlyOpen = (o: {mode: "read-only"}) => Promise<FileSystemSyncAccessHandle>;
        const open = (fh.createSyncAccessHandle as unknown as ReadOnlyOpen).bind(fh);
        const handles: FileSystemSyncAccessHandle[] = [];
        try {
            handles.push(await open({mode: "read-only"}));
            handles.push(await open({mode: "read-only"}));
            return true;
        } catch {
            return false;
        } finally {
            for (const h of handles) h.close();
            await dir.removeEntry("probe").catch(() => undefined);
        }
    },

    /** Put the source into <job>/<input> on OPFS: streamed from a (presigned) URL chunk by chunk, or
     *  written from bytes. Run on ONE worker before prepare(). */
    async stageInput(job: string, input: string, source: {url: string} | {bytes: ArrayBuffer}): Promise<number> {
        const M = Module;
        if (!M) throw new Error("shard worker not initialised");
        const path = `${OPFS_MOUNT}/${job}/${input}`;
        const parts = `${OPFS_MOUNT}/${job}`.split("/").filter(Boolean);
        for (let i = 1, cur = ""; i <= parts.length; i++) {
            cur = "/" + parts.slice(0, i).join("/");
            try {
                M.FS.mkdir(cur);
            } catch {
                /* exists */
            }
        }
        await M.opfsOpen(path, {create: true});
        const stream = M.FS.open(path, "w");
        let pos = 0;
        try {
            if ("url" in source) {
                const resp = await fetch(source.url);
                if (!resp.ok || !resp.body) throw new Error(`fetch source failed: ${resp.status} ${resp.statusText}`);
                const reader = resp.body.getReader();
                for (;;) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    M.FS.write(stream, value, 0, value.byteLength, pos);
                    pos += value.byteLength;
                }
            } else {
                const bytes = new Uint8Array(source.bytes);
                const chunk = 8 << 20;
                for (; pos < bytes.length; pos += chunk) {
                    M.FS.write(stream, bytes, pos, Math.min(chunk, bytes.length - pos), pos);
                }
            }
        } finally {
            M.FS.close(stream);
            await M.opfsDetach(path); // the file stays in OPFS, unlocked for the readers
        }
        return pos;
    },

    async prepare(job: string, input: string, nworkers: number): Promise<number> {
        return need().prepare(`${OPFS_MOUNT}/${job}`, input, nworkers);
    },

    async open(job: string, input: string, lane: number, opts: {deflection: number; angularDeg: number}, nbatches: number): Promise<ShardOpenInfo> {
        const info = await need().open(`${OPFS_MOUNT}/${job}`, input, lane, {
            deflection: opts.deflection,
            angular: opts.angularDeg,
        });
        return {roots: info.roots, huge: info.huge, ends: info.roots >= 0 ? info.batches(nbatches) : []};
    },

    process(begin: number, end: number): number {
        return need().process(begin, end);
    },

    processHuge(h: number, f0: number, f1: number): Promise<number> {
        return need().processHuge(h, f0, f1);
    },

    assembleHuge(h: number, chunk: number): Promise<number> {
        return need().assembleHuge(h, chunk);
    },

    persist(): Promise<boolean> {
        return need().persist();
    },

    merge(nworkers: number, meshopt: boolean): Promise<number> {
        return need().merge(nworkers, meshopt);
    },
};

export type CadGlbShardAPI = typeof api;
Comlink.expose(api);
