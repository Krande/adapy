// Registry of utilities that have an in-browser (wasm) implementation. UtilitiesSection consults this
// before enqueueing a server job: if a wasm utility is registered for the selected utility AND it
// canRun() these kwargs, it runs client-side and falls back to the server on any failure.
//
// Add a new wasm-backed utility: implement WasmUtility (a worker + a runner like diffWasmUtility),
// then register it here under the server utility's name.

import type {WasmUtility} from "@/utils/wasm/wasmUtilityTypes";
import {diffWasmUtility} from "@/utils/diffConverter/diffConverter";

export const wasmUtilities: Record<string, WasmUtility> = {
    diff: diffWasmUtility,
};

export function wasmUtilityFor(name: string | null | undefined): WasmUtility | undefined {
    return name ? wasmUtilities[name] : undefined;
}
