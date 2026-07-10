// Main-thread wrapper for the in-browser native (no-pyodide) STEP -> GLB converter. Spins up the
// stepGlbConverter Web Worker (Comlink) and exposes `nativeStepToGlb`. Mirrors diffConverter.ts.

import * as Comlink from "comlink";

import StepGlbConverterWorker from "./stepGlbConverter.worker.ts?worker&inline";
import type {StepGlbConverterAPI, NativeStepGlbResult} from "./stepGlbConverter.worker";

let worker: Worker | null = null;
let apiRemote: Comlink.Remote<StepGlbConverterAPI> | null = null;

function ensureApi(): Comlink.Remote<StepGlbConverterAPI> {
    if (!apiRemote) {
        worker = new StepGlbConverterWorker();
        apiRemote = Comlink.wrap<StepGlbConverterAPI>(worker);
    }
    return apiRemote;
}

// adapy production tessellation defaults (see adacpp step_to_glb_single / cad_wasm.cpp).
const DEFAULT_DEFLECTION = 2.0;
const DEFAULT_ANGULAR_DEG = 20.0;

/** Convert a STEP buffer to GLB in the worker. The buffer is transferred (consumed). */
export async function nativeStepToGlb(
    stepBytes: ArrayBuffer,
    opts?: {deflection?: number; angularDeg?: number; meshopt?: boolean},
): Promise<NativeStepGlbResult> {
    return ensureApi().stepToGlb(Comlink.transfer(stepBytes, [stepBytes]), {
        deflection: opts?.deflection ?? DEFAULT_DEFLECTION,
        angularDeg: opts?.angularDeg ?? DEFAULT_ANGULAR_DEG,
        meshopt: opts?.meshopt ?? true,
    });
}
