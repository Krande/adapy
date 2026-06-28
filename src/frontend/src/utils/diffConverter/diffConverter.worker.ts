// Web Worker: diff two GLBs entirely in the browser via the OCC-free adacpp_glb_diff wasm module
// (no server job, no server memory). The module (embind) exposes:
//   diffGlb(sceneU8, refU8, mode, tol, rgba) -> { ops:[[node_id,status]], counts, overlay:Uint8Array }
//   status 0=unchanged 1=added 2=removed 3=modified; overlay = a red GLB of the ref-only geometry.
// It parses one model at a time and decodes one mesh node at a time (bounded memory), so it handles
// the meshopt-compressed viewer GLBs without holding the whole model.

import * as Comlink from "comlink";

import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";

const WASM_URL = "/wasm/adacpp_glb_diff.js";

interface DiffRaw {
    ops: [string, number][];
    counts: {added: number; removed: number; modified: number; unchanged: number};
    overlay: Uint8Array;
}
interface EmModule {
    diffGlb(scene: Uint8Array, ref: Uint8Array, mode: string, tol: number, rgba: number): DiffRaw;
}

let modulePromise: Promise<EmModule> | null = null;
function getModule(): Promise<EmModule> {
    if (!modulePromise) modulePromise = loadEmscriptenModule<EmModule>(WASM_URL);
    return modulePromise;
}

export interface WasmDiffResult {
    ops: [string, number][];
    counts: {added: number; removed: number; modified: number; unchanged: number};
    overlay: ArrayBuffer; // empty (byteLength 0) when nothing was removed
    ms: number;
}

const api = {
    async diff(sceneGlb: ArrayBuffer, refGlb: ArrayBuffer, mode: string, tol: number): Promise<WasmDiffResult> {
        const Module = await getModule();
        const t0 = performance.now();
        const r = Module.diffGlb(new Uint8Array(sceneGlb), new Uint8Array(refGlb), mode, tol, 0xd50000ff);
        // r.overlay is a view into the wasm heap -> own, transferable copy.
        const overlay = r.overlay && r.overlay.length ? r.overlay.slice().buffer : new ArrayBuffer(0);
        const ops = r.ops.map((o) => [o[0], o[1]] as [string, number]);
        const counts = {
            added: r.counts.added,
            removed: r.counts.removed,
            modified: r.counts.modified,
            unchanged: r.counts.unchanged,
        };
        const out: WasmDiffResult = {ops, counts, overlay, ms: performance.now() - t0};
        return Comlink.transfer(out, overlay.byteLength ? [overlay] : []);
    },
};

export type DiffConverterAPI = typeof api;
Comlink.expose(api);
