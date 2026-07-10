// Web Worker: native (no-pyodide) B-rep writer — STEP→IFC and IFC→STEP entirely in the browser via
// the OCC-free adacpp_brep_writer embind module (StreamIndex/IfcResolver readers → ifc_emit/step_emit
// emitters; no tessellation, no OCC, no ifcopenshell, no Python). ~1.3 MB. Buffered MEMFS IO; an
// OPFS-streaming tier (mountOpfs) can follow the CAD→GLB pattern for very large B-rep files.
//
// embind surface (adacpp/src/cad/brep_writer_wasm.cpp):
//   stepToIfc(inPath, outPath, schema, deflection, angularDeg, maxSolids) -> products (0 = none/error)
//   ifcToStep(inPath, outPath, deflection, angularDeg, maxSolids)         -> products
//   mountOpfs(mountPoint) -> 0 ok

import * as Comlink from "comlink";

import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";

const WASM_URL = "/wasm/adacpp_brep_writer.js";

// Conversion direction. The wire target (ifc/step) determines which verb runs.
export type BrepDir = "step2ifc" | "ifc2step";

interface EmscriptenFS {
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    unlink(path: string): void;
}
interface EmModule {
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

const api = {
    async convert(
        dir: BrepDir,
        srcBytes: ArrayBuffer,
        opts?: {schema?: string; maxSolids?: number},
    ): Promise<NativeBrepWriteResult> {
        const Module = await getModule();
        const t0 = performance.now();
        const inPath = dir === "step2ifc" ? "/in.step" : "/in.ifc";
        const outPath = dir === "step2ifc" ? "/out.ifc" : "/out.step";
        Module.FS.writeFile(inPath, new Uint8Array(srcBytes));
        const maxSolids = opts?.maxSolids ?? 0;
        const products =
            dir === "step2ifc"
                ? Module.stepToIfc(
                      inPath,
                      outPath,
                      opts?.schema ?? DEFAULT_IFC_SCHEMA,
                      DEFAULT_DEFLECTION,
                      DEFAULT_ANGULAR_DEG,
                      maxSolids,
                  )
                : Module.ifcToStep(inPath, outPath, DEFAULT_DEFLECTION, DEFAULT_ANGULAR_DEG, maxSolids);
        if (products <= 0) {
            throw new Error(`native ${dir === "step2ifc" ? "STEP→IFC" : "IFC→STEP"} wrote no products`);
        }
        const out = Module.FS.readFile(outPath);
        const output = out.slice().buffer;
        try {
            Module.FS.unlink(inPath);
            Module.FS.unlink(outPath);
        } catch {
            /* best-effort cleanup */
        }
        const result: NativeBrepWriteResult = {output, products, ms: performance.now() - t0};
        return Comlink.transfer(result, [output]);
    },
};

export type BrepWriterConverterAPI = typeof api;
Comlink.expose(api);
