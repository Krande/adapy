// The source files the viewer takes: what storage accepts for upload (and the server converts),
// and what opens straight into the scene from disk. Light (the runtime config only), so a menu
// command can build its file picker without loading the upload or conversion code.
import {runtime} from "@/runtime/config";

// Built-in source extensions adapy itself handles (the base worker
// image carries all the parsers). Capability workers can announce
// additional extensions via their registry entry; runtime() pulls
// them from /api/config and ``acceptedSourceExts`` below merges the
// two lists for picker / drag-drop filtering.
export const SUPPORTED_EXTS: readonly string[] = [
    ".glb", ".gltf",
    ".ifc", ".step", ".stp",
    ".xml", ".gnx", ".inp", ".fem",
    ".sat", ".acis",
    ".obj", ".stl", ".ply", ".dae", ".off",
    // Multi-file analysis bundles. Worker unpacks + validates the
    // include chain at convert time; rejects mixed-format / ambiguous
    // entry / missing includes with a clear error.
    ".zip",
    // FEA result formats consumed by the streaming-viewer bake:
    //   .sif  Sesam result database (text-based, gzip-compressible).
    //   .rmed Code_Aster / Salome result file (HDF5).
    ".sif",
    ".rmed",
];

/** What the browser opens itself, with no server: glTF, binary or with its buffers embedded. */
export const LOCAL_SCENE_EXTS: readonly string[] = [".glb", ".gltf"];

/** The upload list: the built-in extensions and the ones the workers advertise. */
export function acceptedSourceExts(): readonly string[] {
    const extra = runtime.extraSourceExts();
    if (extra.length === 0) return SUPPORTED_EXTS;
    const merged = new Set<string>(SUPPORTED_EXTS);
    for (const e of extra) {
        merged.add(e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`);
    }
    return Array.from(merged);
}

/** `name`'s extension, lower case with its dot; "" when it has none. */
export function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}
