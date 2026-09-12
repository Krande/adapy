/**
 * The cellbuilder STATE PACKAGE — public surface.
 *
 * Owns: what the rest of the app may import. The store is composed in
 * ./store from one slice per domain (document, history, selection, cells,
 * placement, loft, systems, catalogs, detailing, blueprints, jobs, view state,
 * relocations, export/import); each slice file's header says what it owns.
 * `@/state/cellBuilderStore` re-exports this module, so every existing import
 * path keeps working.
 *
 * Transport seam: no slice reaches the REST client at runtime — every backend
 * call goes through `capabilities.procedural`, which is what makes the store
 * transport-neutral. The slices DO import DTOs from `services/viewerApi` with
 * `import type`, because those DTOs ARE the wire documents, whichever transport
 * carries them; the type modifier erases the import, so the seam holds.
 */

export {useCellBuilderStore} from "./store";
export {hasEmbeddedDoc} from "./documentSlice";
export {cellsFromDoc} from "./docCodec";

// Re-exported so callers can keep importing the preview-compile gate from the
// store facade; the implementation lives in a store-free util for testability.
export {needsPreviewCompile} from "@/utils/cellbuilder/compileGate";

export type {
    BuilderCell,
    BuilderSelection,
    BuilderSystem,
    CellBuilderMode,
    CompileJobState,
    GizmoMode,
    ModelSnapshot,
    RepresentationMode,
    ResyncSummary,
    SelectMode,
    SystemConnection,
    SystemType,
} from "./types";
export type {CellBuilderState} from "./state";
