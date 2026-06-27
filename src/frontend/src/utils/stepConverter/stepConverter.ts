// Main-thread wrapper for the in-browser STEP->GLB wasm converter (stepConverter.worker.ts).
// Lazily spawns the worker on first use; the worker loads the OCC-free adacpp wasm module and runs the
// conversion off the main thread (and where OPFS sync access handles are usable).

import * as Comlink from "comlink";

import StepConverterWorker from "./stepConverter.worker.ts?worker&inline";
import type { StepConvertOptions, StepConvertResult, StepConverterAPI } from "./stepConverter.worker";

export type { StepConvertOptions, StepConvertResult };

let worker: Worker | null = null;
let api: Comlink.Remote<StepConverterAPI> | null = null;

function ensureApi(): Comlink.Remote<StepConverterAPI> {
  if (!api) {
    worker = new StepConverterWorker();
    api = Comlink.wrap<StepConverterAPI>(worker);
  }
  return api;
}

/** Convert STEP bytes to GLB bytes in-browser. `stepBytes` is transferred (neutered) to the worker. */
export async function convertStepToGlb(
  stepBytes: ArrayBuffer,
  opts?: StepConvertOptions,
): Promise<StepConvertResult> {
  const a = ensureApi();
  return a.convert(Comlink.transfer(stepBytes, [stepBytes]), opts ?? {});
}

/** Tear down the converter worker (frees the wasm instance). */
export function disposeStepConverter(): void {
  worker?.terminate();
  worker = null;
  api = null;
}
