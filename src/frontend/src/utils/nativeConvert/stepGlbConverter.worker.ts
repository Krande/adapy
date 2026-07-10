// Web Worker: STEP -> GLB entirely in the browser via the OCC-free, NO-pyodide adacpp_step_glb wasm
// module (native Part-21 reader + libtess2 + meshoptimizer + GLB writer, embind). This is the
// lightweight counterpart to the pyodide pipeline — a 2.3 MB module, no Python runtime.
//
// embind surface (see adacpp/src/cad/cad_wasm.cpp):
//   stepToGlb(inPath, outPath, spillDir, deflection, angularDeg, meshopt) -> tris (<0 on I/O error)
//   mountOpfs(mountPoint) -> 0 ok
// IO goes through the emscripten FS. This first cut uses the in-heap (WASMFS default) filesystem —
// simplest, and fine for the typical viewer STEP. An OPFS-streaming tier (mountOpfs + pread) can
// follow for multi-GB decks that exceed the wasm32 heap.

import * as Comlink from "comlink";

import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";

const WASM_URL = "/wasm/adacpp_step_glb.js";

interface EmscriptenFS {
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    mkdir(path: string): void;
    unlink(path: string): void;
}
interface EmModule {
    FS: EmscriptenFS;
    stepToGlb(
        inPath: string,
        outPath: string,
        spillDir: string,
        deflection: number,
        angularDeg: number,
        meshopt: boolean,
    ): number;
    mountOpfs(mountPoint: string): number;
}

let modulePromise: Promise<EmModule> | null = null;
function getModule(): Promise<EmModule> {
    if (!modulePromise) modulePromise = loadEmscriptenModule<EmModule>(WASM_URL);
    return modulePromise;
}

export interface NativeStepGlbResult {
    glb: ArrayBuffer;
    tris: number;
    ms: number;
}

const IN_PATH = "/in.step";
const OUT_PATH = "/out.glb";
const SPILL_DIR = "/spill";

const api = {
    async stepToGlb(
        stepBytes: ArrayBuffer,
        opts: {deflection: number; angularDeg: number; meshopt: boolean},
    ): Promise<NativeStepGlbResult> {
        const Module = await getModule();
        const t0 = performance.now();
        Module.FS.writeFile(IN_PATH, new Uint8Array(stepBytes));
        try {
            Module.FS.mkdir(SPILL_DIR);
        } catch {
            /* already exists on a reused module — fine */
        }
        const tris = Module.stepToGlb(IN_PATH, OUT_PATH, SPILL_DIR, opts.deflection, opts.angularDeg, opts.meshopt);
        if (tris < 0) {
            throw new Error("native STEP→GLB failed (I/O error in the wasm module)");
        }
        const out = Module.FS.readFile(OUT_PATH);
        // Own a transferable copy off the wasm heap, then release the FS entries so a reused
        // module doesn't accumulate files across conversions.
        const glb = out.slice().buffer;
        try {
            Module.FS.unlink(IN_PATH);
            Module.FS.unlink(OUT_PATH);
        } catch {
            /* best-effort cleanup */
        }
        const result: NativeStepGlbResult = {glb, tris, ms: performance.now() - t0};
        return Comlink.transfer(result, [glb]);
    },
};

export type StepGlbConverterAPI = typeof api;
Comlink.expose(api);
