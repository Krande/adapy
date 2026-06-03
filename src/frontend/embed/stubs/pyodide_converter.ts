// Embed-only stub for `@/utils/pyodide/pyodide_converter`.
//
// The real module spins up `new Worker(new URL("./pyodide_worker", ...))`,
// which Vite emits as a separate chunk. Even behind a dynamic import, Vite's
// `inlineDynamicImports` build (this embed) follows the edge and emits that
// worker chunk — breaking paradoc's single-file `index.js` consumption. The
// embed never runs an in-browser (Pyodide) conversion, so `vite.config.embed.ts`
// aliases the module to this worker-free stub. The functions are unreachable in
// the embed; they throw if ever called so a regression surfaces loudly rather
// than silently shipping a broken pyodide path.

export type PyodideSourceFormat = "ifc" | "step";

const UNAVAILABLE = "Pyodide conversion is not available in the embed build";

export function isPyodideWorkerReady(): boolean {
    return false;
}

export async function ensurePyodideWorker(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertIfcViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export function shutdownPyodideWorker(): void {
    /* no worker in the embed */
}
