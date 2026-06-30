// Main-thread wrapper for the in-browser GLB diff: spins up the diff Web Worker (Comlink) and exposes
// `runDiffWasm` (two GLB buffers -> classification + overlay) and `diffWasmUtility`, the WasmUtility
// that fetches the scene + ref GLBs and builds the viewer-ops payload (identical shape to the server
// diff @utility, so applyViewerOps is unchanged). Registered in wasmUtilityRegistry under "diff".

import * as Comlink from "comlink";

import DiffConverterWorker from "./diffConverter.worker.ts?worker&inline";
import type {DiffConverterAPI, WasmDiffResult} from "./diffConverter.worker";
import {viewerApi, type ScopeUrl} from "@/services/viewerApi";
import type {ViewerOp, ViewerOpsPayload} from "@/utils/scene/apply_viewer_ops";
import type {WasmUtility, WasmUtilityContext} from "@/utils/wasm/wasmUtilityTypes";

let worker: Worker | null = null;
let apiRemote: Comlink.Remote<DiffConverterAPI> | null = null;

function ensureApi(): Comlink.Remote<DiffConverterAPI> {
    if (!apiRemote) {
        worker = new DiffConverterWorker();
        apiRemote = Comlink.wrap<DiffConverterAPI>(worker);
    }
    return apiRemote;
}

/** Diff two GLB buffers in the worker. Buffers are transferred (consumed) into the worker. */
export async function runDiffWasm(
    sceneGlb: ArrayBuffer,
    refGlb: ArrayBuffer,
    mode: string,
    tol: number,
): Promise<WasmDiffResult> {
    return ensureApi().diff(Comlink.transfer(sceneGlb, [sceneGlb]), Comlink.transfer(refGlb, [refGlb]), mode, tol);
}

// Colours + status->colour mirror the server diff utility (ada/comms/rest/utilities/diff.py).
const C = {added: "#00c853", removed: "#d50000", modified: "#ff9100", unchanged: "#9e9e9e"};
const STATUS_COLOR: Record<number, string> = {0: C.unchanged, 1: C.added, 3: C.modified};
// frontend diff_type -> native match mode. byCoverage has no wasm core (per-cell binning) -> server.
const NATIVE_MODE: Record<string, string> = {byName: "byName", byCentroid: "byCentroid", byProperty: "byProperty"};

async function fetchGlbBytes(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
    // Prefer a presigned URL (direct from S3, no API proxy); fall back to the authed blob endpoint.
    try {
        const p = await viewerApi.requestDownloadUrl(scope, key);
        const r = await fetch(p.url);
        if (r.ok) return await r.arrayBuffer();
    } catch {
        /* fall through to the authed blob */
    }
    return viewerApi.getBlob(scope, key);
}

export const diffWasmUtility: WasmUtility = {
    canRun(ctx: WasmUtilityContext): boolean {
        const dt = String(ctx.kwargs.diff_type ?? "byName");
        return !!ctx.refKey && dt in NATIVE_MODE; // no ref, or byCoverage -> server path
    },
    async run(ctx: WasmUtilityContext): Promise<ViewerOpsPayload> {
        const dt = String(ctx.kwargs.diff_type ?? "byName");
        const mode = NATIVE_MODE[dt] ?? "byName";
        const tol = Number(ctx.kwargs.tolerance ?? 0.001);
        const showOverlay = ctx.kwargs.show_removed_overlay !== false;

        const [sceneGlb, refGlb] = await Promise.all([
            fetchGlbBytes(ctx.scope, ctx.sourceKey),
            fetchGlbBytes(ctx.scope, ctx.refKey),
        ]);
        const r = await runDiffWasm(sceneGlb, refGlb, mode, tol);

        const elements = r.ops.map(([key, st]) => ({key, color: STATUS_COLOR[st] ?? C.unchanged}));
        const ops: ViewerOp[] = [{op: "color_elements", elements}];
        if (showOverlay && r.counts.removed > 0 && r.overlay.byteLength > 0) {
            ops.push({op: "add_overlay_geometry", blob: r.overlay, label: "removed", color: C.removed});
        }
        const legend = [
            {label: "added", color: C.added, count: r.counts.added},
            {label: "modified", color: C.modified, count: r.counts.modified},
            {label: "removed", color: C.removed, count: r.counts.removed},
            {label: "unchanged", color: C.unchanged, count: r.counts.unchanged},
        ];
        const summary: Record<string, unknown> = {
            compare_ref: ctx.refKey,
            diff_type: dt,
            ...r.counts,
            engine: "wasm",
            wasm_ms: Math.round(r.ms),
        };
        return {version: 1, ops, legend, summary};
    },
};
