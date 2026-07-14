// Web Worker: native (no-pyodide) B-rep writer — STEP→IFC and IFC→STEP entirely in the browser via
// the OCC-free adacpp_brep_writer embind module (StreamIndex/IfcResolver readers → ifc_emit/step_emit
// emitters; no tessellation, no OCC, no ifcopenshell, no Python). ~1.3 MB. Buffered MEMFS path +
// OPFS-streaming tier (mountOpfs) for very large B-rep files, sharing opfsWasmfs with cadGlbConverter.
//
// embind surface (adacpp/src/cad/brep_writer_wasm.cpp):
//   stepToIfc(inPath, outPath, schema, deflection, angularDeg, maxSolids) -> products (0 = none/error)
//   ifcToStep(inPath, outPath, deflection, angularDeg, maxSolids)         -> products
//   mountOpfs(mountPoint) -> 0 ok

import * as Comlink from "comlink";

import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";
import {WasmfsModule, OPFS_MOUNT, ensureOpfsMounted, streamUrlToOpfs, unlinkAll} from "./opfsWasmfs";

const WASM_URL = "/wasm/adacpp_brep_writer.js";

// Conversion direction. The wire target (ifc/step) determines which verb runs.
export type BrepDir = "step2ifc" | "ifc2step";

interface EmscriptenFS {
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    unlink(path: string): void;
    mkdir(path: string): void;
    open(path: string, flags: string): unknown;
    write(stream: unknown, buffer: Uint8Array, offset: number, length: number, position: number): number;
    close(stream: unknown): void;
}
interface EmModule extends WasmfsModule {
    FS: EmscriptenFS;
    stepToIfc(
        inPath: string,
        outPath: string,
        schema: string,
        deflection: number,
        angularDeg: number,
        maxSolids: number,
    ): number;
    ifcToStep(inPath: string, outPath: string, deflection: number, angularDeg: number, maxSolids: number): number;
    mountOpfs(mountPoint: string): number;
}

let modulePromise: Promise<EmModule> | null = null;
function getModule(): Promise<EmModule> {
    if (!modulePromise) modulePromise = loadEmscriptenModule<EmModule>(WASM_URL);
    return modulePromise;
}

export interface NativeBrepWriteResult {
    output: ArrayBuffer;
    products: number;
    ms: number;
}

// AP242 face-set fallback tolerances (only used for geometry the analytic emitter can't represent);
// match the adapy production defaults. IFC schema for the STEP→IFC direction.
const DEFAULT_DEFLECTION = 2.0;
const DEFAULT_ANGULAR_DEG = 20.0;
const DEFAULT_IFC_SCHEMA = "IFC4X3_ADD2";

const srcExt = (dir: BrepDir) => (dir === "step2ifc" ? "step" : "ifc");
const outExt = (dir: BrepDir) => (dir === "step2ifc" ? "ifc" : "step");

// Run the writer verb for `dir` on inPath → outPath. Returns products (>0 success).
function runWriter(Module: EmModule, dir: BrepDir, inPath: string, outPath: string, schema: string, maxSolids: number) {
    return dir === "step2ifc"
        ? Module.stepToIfc(inPath, outPath, schema, DEFAULT_DEFLECTION, DEFAULT_ANGULAR_DEG, maxSolids)
        : Module.ifcToStep(inPath, outPath, DEFAULT_DEFLECTION, DEFAULT_ANGULAR_DEG, maxSolids);
}

const api = {
    // Can this worker OPFS-stream? (mount is the capability gate; worker-only.)
    async opfsAvailable(): Promise<boolean> {
        return ensureOpfsMounted(await getModule());
    },

    // Buffered path: source bytes → MEMFS → output. Simplest; fine below the OPFS threshold.
    async convert(
        dir: BrepDir,
        srcBytes: ArrayBuffer,
        opts?: {schema?: string; maxSolids?: number},
    ): Promise<NativeBrepWriteResult> {
        const Module = await getModule();
        const t0 = performance.now();
        const inPath = `/in.${srcExt(dir)}`;
        const outPath = `/out.${outExt(dir)}`;
        Module.FS.writeFile(inPath, new Uint8Array(srcBytes));
        const products = runWriter(Module, dir, inPath, outPath, opts?.schema ?? DEFAULT_IFC_SCHEMA, opts?.maxSolids ?? 0);
        if (products <= 0) {
            throw new Error(`native ${dir === "step2ifc" ? "STEP→IFC" : "IFC→STEP"} wrote no products`);
        }
        const out = Module.FS.readFile(outPath);
        const output = out.slice().buffer;
        unlinkAll(Module, [inPath, outPath]);
        const result: NativeBrepWriteResult = {output, products, ms: performance.now() - t0};
        return Comlink.transfer(result, [output]);
    },

    // OPFS-streaming path: stream a (presigned) URL into OPFS through WASMFS, write off-disk, read the
    // output back through WASMFS. For B-rep files too large to buffer in the wasm heap.
    async convertStreaming(
        dir: BrepDir,
        sourceUrl: string,
        opts?: {schema?: string; maxSolids?: number},
    ): Promise<NativeBrepWriteResult> {
        const Module = await getModule();
        if (!ensureOpfsMounted(Module)) {
            throw new Error("OPFS streaming unavailable in this worker (OPFS backend not mountable)");
        }
        const t0 = performance.now();
        const inPath = `${OPFS_MOUNT}/adacpp_brep_in.${srcExt(dir)}`;
        const outPath = `${OPFS_MOUNT}/adacpp_brep_out.${outExt(dir)}`;
        await streamUrlToOpfs(Module, inPath, sourceUrl);
        const cleanup = () => unlinkAll(Module, [inPath, outPath]);
        const products = runWriter(Module, dir, inPath, outPath, opts?.schema ?? DEFAULT_IFC_SCHEMA, opts?.maxSolids ?? 0);
        if (products <= 0) {
            cleanup();
            throw new Error(`native streaming ${dir === "step2ifc" ? "STEP→IFC" : "IFC→STEP"} wrote no products`);
        }
        const out = Module.FS.readFile(outPath);
        const output = out.slice().buffer;
        cleanup();
        const result: NativeBrepWriteResult = {output, products, ms: performance.now() - t0};
        return Comlink.transfer(result, [output]);
    },
};

export type BrepWriterConverterAPI = typeof api;
Comlink.expose(api);
