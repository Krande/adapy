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

// Keep the export list in step with the real module: a name the real module
// gained and this stub lacks fails the embed build ("X is not exported by
// embed/stubs/pyodide_converter.ts").

export type PyodideSourceFormat = "ifc" | "step" | "mesh" | "sat" | "fea" | "fea_glb" | "fem" | "genie";

export interface PyodideEngineWheel {
    entrypoint: string;
    deps: string[];
    url: string;
}

const UNAVAILABLE = "Pyodide conversion is not available in the embed build";

export function isPyodideWorkerReady(): boolean {
    return false;
}

export async function ensurePyodideWorker(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export function prewarmPyodide(): void {
    /* no worker in the embed */
}

export async function convertViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function compileProceduralViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertViaPyodideStream(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertViaPyodideFeaBakeStream(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertIfcViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export function shutdownPyodideWorker(): void {
    /* no worker in the embed */
}
