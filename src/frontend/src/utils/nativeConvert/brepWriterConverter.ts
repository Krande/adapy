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

/** Does this browser/worker support the OPFS-streaming tier for the B-rep writer? */
export async function nativeBrepWriterOpfsAvailable(): Promise<boolean> {
    try {
        return await ensureApi().opfsAvailable();
    } catch {
        return false;
    }
}

/** Convert a STEP/IFC at a (presigned, streamable) URL, streaming the source through OPFS so a large
 * B-rep file never has to fit the wasm heap. Requires nativeBrepWriterOpfsAvailable(). */
export async function nativeBrepWriteStreaming(
    dir: BrepDir,
    sourceUrl: string,
    opts?: {schema?: string; maxSolids?: number},
): Promise<NativeBrepWriteResult> {
    return ensureApi().convertStreaming(dir, sourceUrl, opts);
}
