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

// NOTE: this stub must export EVERY symbol the real module exports that any
// bundled module imports — rollup resolves the alias at build time and a missing
// export is a hard build failure, not a runtime one. Adding an export to
// pyodide_converter.ts means adding it here too. (This drifted once already:
// the streaming entry points landed in #225 and the embed build stayed broken
// for two months because nothing exercised it. embed/dev.html now does.)
export type PyodideSourceFormat = "ifc" | "step" | "mesh" | "sat" | "fea" | "fea_glb" | "fem" | "genie";

/** Mirrors the real module's wheel descriptor so importers type-check. */
export interface PyodideEngineWheel {
    name: string;
    url: string;
}

const UNAVAILABLE = "Pyodide conversion is not available in the embed build";

export function isPyodideWorkerReady(): boolean {
    return false;
}

export async function ensurePyodideWorker(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

/** No-op: the embed has no worker to pre-warm, and callers treat this as fire-and-forget. */
export function prewarmPyodide(): void {
    /* no worker in the embed */
}

export async function convertViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertViaPyodideStream(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertViaPyodideFeaBakeStream(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function compileProceduralViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export async function convertIfcViaPyodide(): Promise<never> {
    throw new Error(UNAVAILABLE);
}

export function shutdownPyodideWorker(): void {
    /* no worker in the embed */
}
