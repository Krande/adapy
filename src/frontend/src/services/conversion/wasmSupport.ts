// Single source of truth for "can the in-browser (WASM) engine do this
// conversion?". Shared by the routing decision (shouldUsePyodide), the
// pyodide pipeline's format detection, and — later — the WASM audit-run
// sweep, so they never disagree on the supported matrix.

import type {PyodideSourceFormat} from "@/utils/pyodide/pyodide_converter";
import type {TargetFormat} from "@/services/viewerApi";

// Mesh formats trimesh can load+export to GLB without any CAD kernel.
// (.glb is excluded — a glb source is served as-is, no conversion.)
const WASM_MESH_EXTS = ["obj", "stl", "ply", "gltf", "dae", "off"] as const;

// FEA result sources the browser can bake into the streaming-viewer
// artefact tree (h5py for .rmed/.med; pure-python for .sif/.sin). These
// don't produce a single GLB — they go through the FEA bake path, not the
// GLB conversion path — so they're matched separately.
const WASM_FEA_EXTS = ["rmed", "med", "sif", "sin"] as const;

export interface WasmFormat {
    format: PyodideSourceFormat;
    ext: string;
}

function extOf(sourceKey: string): string {
    const lower = sourceKey.toLowerCase();
    const dot = lower.lastIndexOf(".");
    return dot >= 0 ? lower.slice(dot + 1) : "";
}

/**
 * Classify a source key into the pyodide stack that handles it, or
 * null if the WASM engine can't convert it. Increment 1 covers the
 * geometry→GLB formats that need no full adapy wheel:
 *   - .ifc        → ifcopenshell-wasm + trimesh
 *   - .step/.stp  → adacpp.cad (OCCT cross-compiled)
 *   - mesh files  → trimesh
 * (SAT + FEM arrive with the adapy pyodide wheel.)
 */
export function detectWasmFormat(sourceKey: string): WasmFormat | null {
    const ext = extOf(sourceKey);
    if (ext === "ifc") return {format: "ifc", ext};
    if (ext === "step" || ext === "stp") return {format: "step", ext};
    if (ext === "sat" || ext === "acis") return {format: "sat", ext};
    if ((WASM_MESH_EXTS as readonly string[]).includes(ext)) return {format: "mesh", ext};
    return null;
}

/**
 * True if the WASM engine can bake this FEA result source into the
 * streaming-viewer artefact tree (the FEA bake path, distinct from the
 * GLB conversion path). Engine-agnostic.
 */
export function isWasmFeaSource(sourceKey: string): boolean {
    return (WASM_FEA_EXTS as readonly string[]).includes(extOf(sourceKey));
}

/**
 * True if the WASM engine can produce ``targetFormat`` from ``sourceKey``.
 * Only GLB output is supported in-browser today. Engine-agnostic — does
 * NOT consult the user's conversion-engine toggle.
 */
export function wasmSupportsConversion(sourceKey: string, targetFormat: TargetFormat): boolean {
    if (targetFormat !== "glb") return false;
    return detectWasmFormat(sourceKey) !== null;
}
