import {runtime} from "@/runtime/config";

// File-kind predicates shared by the storage browser and the scene
// panel's loaded-models list. Extracted from StorageBrowser so the
// unload/visibility code paths agree on which files are streaming-FEA
// (loaded via replace_model, torn down via clear_loaded_model) vs
// regular overlays (per-source groups).

// Files that carry per-(step, field) result data and benefit from the
// picker UI. .sif (Sesam text) and .sin (Sesam Norsam binary) both
// carry the same record schema and converter; new formats land
// here when their converter learns to honor (step, field).
export function isFEAResult(name: string): boolean {
    const lower = name.toLowerCase();
    return lower.endsWith(".sif") || lower.endsWith(".sin");
}

// Files that flow through the streaming-viewer artefact bake (mesh
// GLB + per-field blobs + manifest). Static set: .sif, .sin, and
// .rmed are adapy-native streaming sources. Capability workers (e.g.
// abaqus .odb / .sqlite) advertise additional extensions through
// /api/config → window.STREAMING_ONLY_EXTS; honoring that here is
// what keeps a plug-in's stream-readable formats from accidentally
// hitting the legacy /convert pipeline (415) on click.
export function isStreamingFEAResult(name: string): boolean {
    const lower = name.toLowerCase();
    if (lower.endsWith(".sif") || lower.endsWith(".sin") || lower.endsWith(".rmed")) return true;
    // Design-model FEM meshes now load through the same streaming bake (mesh + beam-solids,
    // clickable + tree), so FE-mesh viewing is one path. They stay legacy-convertible on the
    // /convert page (like .sif).
    if (lower.endsWith(".inp") || lower.endsWith(".fem") || lower.endsWith(".med")) return true;
    for (const e of runtime.streamingOnlyExts()) {
        const norm = e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`;
        if (lower.endsWith(norm)) return true;
    }
    return false;
}

// Files the legacy "load into scene" path can handle — those that
// have a usable GLB target via the legacy convert pipeline. Mirror of
// ada.comms.rest.converter.supported_targets_for: anything in
// _STREAMING_FEA_EXTS (or a worker-advertised streaming-only
// extension) has no legacy GLB target, only the streaming bake.
export function canLoadIntoSceneLegacy(name: string): boolean {
    const lower = name.toLowerCase();
    if (lower.endsWith(".rmed")) return false;
    for (const e of runtime.streamingOnlyExts()) {
        const norm = e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`;
        if (lower.endsWith(norm)) return false;
    }
    return true;
}
