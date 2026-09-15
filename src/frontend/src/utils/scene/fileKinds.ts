import {runtime} from "@/runtime/config";
import {isRenderableByPlugin} from "@/plugins/registry";

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

// The extension of a storage key, lower-cased and dot-prefixed, or "" when it
// has none. Folder-aware: only the last path segment is inspected, so a key
// like "2026.05/model" has no extension rather than one of ".05/model".
function extensionOf(key: string): string {
    const base = key.toLowerCase().split("/").pop() ?? "";
    const dot = base.lastIndexOf(".");
    return dot > 0 ? base.slice(dot) : "";
}

// Source extensions adapy's OWN converters register a GLB target for. A
// fallback for when the live matrix consulted below is unavailable.
//
// This is a mirror, and mirrors drift — so it is used only where there is
// nothing better, never in preference to what the server says. Transcribed from
// the ConverterRegistry registrations naming "glb" as a target in
// ada/comms/rest/converters/:
//
//   _PASSTHROUGH_EXTS   .glb                            (copied, not converted)
//   trimesh             .gltf .obj .stl .ply .dae .off
//   _ADA_LOADABLE_EXTS  .ifc .step .stp .xml .gnx .inp .fem .sat .acis
//   _FEA_RESULT_EXTS    .sif .sin
//
// .med is deliberately absent: it converts to .inp/.fem and to nothing else,
// and it reaches the scene through the streaming bake instead (see
// isStreamingFEAResult). .zip is handled separately, below.
const LEGACY_GLB_SOURCE_EXTS: ReadonlySet<string> = new Set([
    ".glb",
    ".gltf",
    ".obj",
    ".stl",
    ".ply",
    ".dae",
    ".off",
    ".ifc",
    ".step",
    ".stp",
    ".xml",
    ".gnx",
    ".inp",
    ".fem",
    ".sat",
    ".acis",
    ".sif",
    ".sin",
]);

// Multi-file analysis bundles. The server registers no converter for .zip at
// all — a bundle unpacks to a single Abaqus deck, and supported_targets_for
// answers for it by delegating to .inp (its _BUNDLE_EXTS branch). A client
// reading only the registry-derived matrix would therefore conclude a bundle is
// unloadable, which is wrong, so the same delegation is spelled out here.
const BUNDLE_EXT = ".zip";
const BUNDLE_INNER_EXT = ".inp";

// Files the legacy "load into scene" path can handle — those with a usable GLB
// target through the legacy convert pipeline.
//
// AN ALLOWLIST, WHICH IS WHAT "MIRROR OF supported_targets_for" ALWAYS MEANT.
// This function used to be a denylist: everything is loadable except .rmed and
// the worker-advertised streaming-only extensions. That is not what the server
// function it claimed to mirror does. supported_targets_for returns the targets
// an extension HAS — [] for one it has never heard of — so the faithful
// question is "does this extension have a GLB target", and anything unknown
// answers no. Under the denylist every unknown extension was assumed loadable,
// so a .db sitting in a scope was walked by the gallery and ticked into the load
// queue, where it could only fail: the convert POST 415s because no converter
// accepts it. The class was wrong, not the one extension.
//
// WHERE THE TRUTH COMES FROM. /api/config's conversionMatrix is the union of
// what every LIVE worker advertises (source extension → target formats),
// computed from the very ConverterRegistry that backs supported_targets_for. It
// is already fetched at boot, so consulting it costs nothing per file — which
// matters, because this predicate runs once per file over a whole listing on
// every render to build the gallery's walk list.
//
// WHY NOT ASK THE SERVER PER FILE. There is a route that answers exactly this:
// GET /api/scopes/{scope}/convert/targets?source_key=… . It is the wrong tool
// twice over. It is one request per file, so a gallery of three hundred files is
// three hundred round trips to decide what to draw; and it cannot tell you
// anything the matrix cannot, because supported_targets_for reads nothing from
// the key except its EXTENSION. A per-file question with a per-extension answer.
//
// The static mirror above is used only when the matrix is empty — a deployment
// with the queue disabled, or one whose page loaded before any worker
// registered. Without that fallback the second case would empty the storage
// browser of every loadable file until the next reload.
export function canLoadIntoSceneLegacy(name: string): boolean {
    const ext = extensionOf(name);
    if (!ext) return false;
    // A GLB is loaded as-is, so it is the one thing that stays openable on a
    // deployment with no conversion at all.
    if (ext === ".glb") return true;
    // Everything else reaches the scene through /convert, which does not exist
    // when the queue is disabled: overlay_file_in_scene refuses such a load with
    // a console warning, so claiming otherwise here is a checkbox that silently
    // does nothing.
    if (!runtime.convertEnabled()) return false;
    // A worker-advertised streaming-only extension has no legacy GLB target by
    // construction (the server computes that set as "advertised minus
    // legacy-convertible"). Kept as an explicit guard rather than left to the
    // matrix: the API's legacy set is its OWN converter registry, so an
    // extension that only a capability worker converts can appear in both lists,
    // and this keeps such a file on the streaming path isStreamingFEAResult
    // already routes it to.
    for (const e of runtime.streamingOnlyExts()) {
        const norm = e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`;
        if (ext === norm) return false;
    }
    const lookupExt = ext === BUNDLE_EXT ? BUNDLE_INNER_EXT : ext;
    const matrix = runtime.conversionMatrix();
    if (matrix.length > 0) return runtime.conversionTargetsFor(lookupExt).includes("glb");
    return LEGACY_GLB_SOURCE_EXTS.has(lookupExt);
}

// The question every file affordance in the viewer actually asks: can this file
// be put in the scene by SOME route?
//
// The three routes are core's streaming bake, core's legacy convert pipeline,
// and a plugin that registered a renderable-file provider for this kind of key
// (plugin API 1.5.0). The call sites used to spell the first two out as
// `isStreamingFEAResult(n) || canLoadIntoSceneLegacy(n)` in five places, which
// meant the third would have had to be added in five places — and forgotten in
// one.
//
// THE PLUGIN TERM IS HALF THE REASON THIS FUNCTION EXISTS. Tightening
// canLoadIntoSceneLegacy into an allowlist is what stops the gallery opening a
// .db it cannot render. But a plugin that CAN render that .db needs the file to
// still be offered — a core-only allowlist would filter it out before the
// provider was ever asked, and the two changes would cancel each other out. The
// registry lookup is a synchronous walk of registered providers with no I/O,
// which is what makes it affordable here (see isRenderableByPlugin).
//
// With no plugin registered the third term is constant false and this is exactly
// the first two — which is the deployed default, and the case that has to stay
// correct.
export function canOpenInScene(name: string): boolean {
    return (
        isStreamingFEAResult(name) ||
        canLoadIntoSceneLegacy(name) ||
        isRenderableByPlugin(name)
    );
}
