// Contract for an in-browser (wasm) implementation of a worker @utility. A wasm utility produces the
// SAME viewer-ops payload the server @utility would, but computes it client-side (no NATS job, no
// server memory) — so applyViewerOps and the whole result UI stay identical. See wasmUtilityRegistry.

import type {ScopeUrl} from "@/services/viewerApi";
import type {ViewerOpsPayload} from "@/utils/scene/apply_viewer_ops";

export interface WasmUtilityContext {
    scope: ScopeUrl;
    sourceKey: string; // the loaded scene GLB key
    refKey: string; // resolved compare/ref GLB key ("" if the utility has none)
    kwargs: Record<string, string | number | boolean | null>;
}

export interface WasmUtility {
    /** Cheap predicate: can THIS run (these kwargs) be done in-browser? If false, the caller uses the
     *  server path (e.g. diff's byCoverage mode has no wasm core, or no ref is selected). */
    canRun(ctx: WasmUtilityContext): boolean;
    /** Produce the viewer-ops payload client-side. */
    run(ctx: WasmUtilityContext): Promise<ViewerOpsPayload>;
}
