// Single source of truth for "can the in-browser (WASM) engine do this
// conversion?". Shared by the routing decision (shouldUsePyodide), the
// pyodide pipeline's format detection, and — later — the WASM audit-run
// sweep, so they never disagree on the supported matrix.

import type {PyodideSourceFormat} from "@/utils/pyodide/pyodide_converter";
import type {TargetFormat} from "@/services/viewerApi";

// Mesh formats trimesh can load + re-export (to glb/obj/stl) without any CAD
// kernel. glb is a valid *source* here too (glb→obj/stl); glb→glb is excluded
// as a no-op by the self-conversion guard in wasmSupportsConversion.
const WASM_MESH_EXTS = ["obj", "stl", "ply", "gltf", "dae", "off", "glb"] as const;

// FEM input decks adapy reads with the pure-python ada.from_fem (Abaqus .inp,
// Sesam .fem). create_concept_objects rebuilds physical Beam/Plate objects so
// the deck exports to glb/ifc/step/xml/obj/stl — all on the WASM FEM stack
// (proven via the audit wasm-sweep probe). Deck→deck rewrites (→fem/med) need
// multi-file (zip) output and are intentionally not here yet.
const WASM_FEM_DECK_EXTS = ["inp", "fem"] as const;

// FEA result sources the browser can bake into the streaming-viewer
// artefact tree (h5py for .rmed/.med; pure-python for .sif/.sin). These
// don't produce a single GLB — they go through the FEA bake path, not the
// GLB conversion path — so they're matched separately.
const WASM_FEA_EXTS = ["rmed", "med", "sif", "sin"] as const;

export interface WasmFormat {
    format: PyodideSourceFormat;
    ext: string;
}

// Target formats each WASM source-format can actually PRODUCE in-browser.
// This is the verified set, not the aspirational one — keep it honest so the
// router never sends the engine a cell that fatally aborts or silently fails:
//   - sat/acis: from_acis (pure-python) → to_{gltf,trimesh,stp,genie_xml}. IFC
//     output is omitted pending an adacpp build_advanced_face_planar wasm fix.
//   - ifc:      from_ifc (ifcopenshell-wasm) → every writer incl. to_ifc.
//   - step/stp: GLB only — from_step defaults to the OCC reader (not wasm-safe);
//     non-GLB targets wait on routing read_step_file through the CadBackend.
//   - mesh:     trimesh → glb (mesh→mesh round-trips aren't worker-registry pairs).
//
// NOT sourced from window.CONVERSION_MATRIX, and deliberately so. That matrix is
// what the WORKER pools advertise; this is what THIS BROWSER can do, and the two
// are different questions with different answers — the notes above are limits of
// the wasm build (pyodide wheels, embind modules) that no worker has or reports.
// Driving this off the matrix would also invert the dependency: it is empty when
// the queue is disabled (dev / desktop / embed) or when no worker is live, which
// is exactly when the in-browser engine is the ONLY one — the SPA would forget
// its own capabilities because a server pool was down.
//
// The serializer/tessellator DROPDOWN is a separate axis and *is* schema-driven
// from the matrix (see serializerMatrix.ts); this table only decides which
// (source, target) cells the browser can be handed at all.
const WASM_TARGETS_BY_FORMAT: Record<PyodideSourceFormat, readonly string[]> = {
    sat: ["glb", "obj", "stl", "step", "xml", "ifc"],
    ifc: ["glb", "obj", "stl", "step", "xml", "ifc"],
    // STEP via the kernel-free stream reader (from_step reader="stream") for
    // non-GLB; GLB stays on the adacpp fast path.
    step: ["glb", "ifc", "xml", "stl", "obj", "step"],
    mesh: ["glb", "obj", "stl"],
    // FEM decks: from_fem geometry writers + deck↔deck rewrites (inp/fem/med);
    // identity pairs (inp→inp, fem→fem) excluded by the self-conversion guard.
    fem: ["glb", "ifc", "step", "xml", "obj", "stl", "inp", "fem", "med"],
    // Genie xml: from_genie_xml → geometry writers.
    genie: ["glb", "ifc", "step", "xml", "obj", "stl"],
    fea: [], // FEA sources go through the bake path (isWasmFeaSource), not this matrix
    // SIF/SIN result → single tessellated GLB (read_sif/read_sin → FEAResult
    // .to_gltf, all pure-python+numpy+trimesh). This is the registry's lone
    // target for those sources and is distinct from the bake tree (fea), so
    // the audit sweep can run these cells in-browser instead of skipping them.
    fea_glb: ["glb"],
};

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
    // Sesam SIF/SIN results convert to a single GLB via FEAResult.to_gltf
    // (the fea_glb stack). Note these are ALSO bake sources (isWasmFeaSource);
    // the bake path is selected separately by the FEA-viewer flow, this maps
    // only the registry's single-GLB conversion cell.
    if (ext === "sif" || ext === "sin") return {format: "fea_glb", ext};
    if ((WASM_FEM_DECK_EXTS as readonly string[]).includes(ext)) return {format: "fem", ext};
    if (ext === "xml") return {format: "genie", ext};
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
 * Consults the per-source target matrix (WASM_TARGETS_BY_FORMAT) so non-GLB
 * outputs (obj/stl/step/xml/ifc) route in-browser for the sources that
 * support them. Engine-agnostic — does NOT consult the conversion-engine
 * toggle (the router does that separately).
 */
export function wasmSupportsConversion(sourceKey: string, targetFormat: TargetFormat): boolean {
    const detected = detectWasmFormat(sourceKey);
    if (!detected) return false;
    // No-op self-conversions aren't real conversions: glb→glb (a glb source is
    // served as-is) and the FEM deck identity pairs inp→inp / fem→fem. ifc→ifc
    // / xml→xml / step→step are genuine writer round-trips and stay supported.
    if ((detected.format === "mesh" || detected.format === "fem") && targetFormat === detected.ext) {
        return false;
    }
    return WASM_TARGETS_BY_FORMAT[detected.format].includes(targetFormat);
}
