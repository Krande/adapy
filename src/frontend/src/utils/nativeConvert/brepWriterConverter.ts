// Main-thread wrapper for the in-browser native (no-pyodide) B-rep writer (STEP→IFC / IFC→STEP).
// Spins up the brepWriterConverter Web Worker (Comlink). Mirrors cadGlbConverter.ts.

import * as Comlink from "comlink";

import BrepWriterConverterWorker from "./brepWriterConverter.worker.ts?worker&inline";
import type {BrepWriterConverterAPI, NativeBrepWriteResult, BrepDir} from "./brepWriterConverter.worker";

export type {BrepDir, NativeBrepWriteResult} from "./brepWriterConverter.worker";

let worker: Worker | null = null;
let apiRemote: Comlink.Remote<BrepWriterConverterAPI> | null = null;

function ensureApi(): Comlink.Remote<BrepWriterConverterAPI> {
    if (!apiRemote) {
        worker = new BrepWriterConverterWorker();
        apiRemote = Comlink.wrap<BrepWriterConverterAPI>(worker);
    }
    return apiRemote;
}

/** Convert a STEP/IFC buffer to the other B-rep format in the worker. The buffer is transferred. */
export async function nativeBrepWrite(
    dir: BrepDir,
    srcBytes: ArrayBuffer,
    opts?: {schema?: string; maxSolids?: number},
): Promise<NativeBrepWriteResult> {
    return ensureApi().convert(dir, Comlink.transfer(srcBytes, [srcBytes]), opts);
}
