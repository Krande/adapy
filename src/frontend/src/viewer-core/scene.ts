// `@/viewer-core/scene` — the 3D half of the shell contract.
//
// Split from `@/viewer-core` by WEIGHT, not by taste: this entry point pulls the
// canvas, the loaders and the FEA streaming path (hundreds of KB), so a shell
// profile that never shows a model — a /convert or /admin page — must not import
// it, exactly as core's own `app.tsx` keeps those routes off `CanvasWrapper`.
//
// Everything here assumes an enclosing `<AdaViewerProvider>` from `@/viewer-core`.
//
// See `@/viewer-core` for the contract rules (leaf re-exports only, no
// `@/plugins` barrel, additions are contract decisions).

// ---------------------------------------------------------------------------
// The canvas. `CanvasWrapper` is the whole scene: renderer, camera, controls,
// lights, gizmo, pointer handling and the feature controllers. A shell mounts it
// and sizes its container — the canvas resizes itself via its ResizeObserver, so
// it must never be re-parented or covered by a shell's layout.
// ---------------------------------------------------------------------------
export { default as CanvasWrapper } from "@/components/viewer/CanvasWrapper";
export type { CanvasWrapperProps } from "@/components/viewer/CanvasWrapper";

// ---------------------------------------------------------------------------
// Deep links. Handles `?file=`, `?gltf=`, procedural/sim params and routes FEA
// results to the streaming path. A shell that skips this silently breaks every
// shared viewer link.
// ---------------------------------------------------------------------------
export { useUrlParamLoad } from "@/hooks/useUrlParamLoad";

// ---------------------------------------------------------------------------
// Loading + unloading model sources.
// ---------------------------------------------------------------------------
export {
  load_glb_by_url_rest,
  load_glb_from_bytes,
  view_file_object_from_server,
} from "@/utils/scene/handlers/view_file_object_from_server";
export { derivedKeyForGlb, overlay_file_in_scene } from "@/utils/scene/handlers/overlay_file_in_scene";
export { unload_any_source } from "@/utils/scene/handlers/unload_any_source";
export { clear_loaded_model } from "@/utils/scene/handlers/clear_loaded_model";
export { applySideBySideOffset } from "@/utils/scene/handlers/side_by_side";

// ---------------------------------------------------------------------------
// FEA streaming. The active mesh + its per-element selection: what a shell needs
// to drive results UI off the same objects core paints and deforms.
// ---------------------------------------------------------------------------
export {
  getActiveFeaMesh,
  getActiveFeaSelectedRangeIds,
  setActiveFeaSelectedRangeIds,
  setFeaUndeformedGhost,
  setFeaElementEdgesVisible,
  setFeaResultColorsVisible,
  hasFeaElementEdges,
} from "@/utils/scene/handlers/load_fea_streaming";
export {
  selectFeaResultComponent,
  selectFeaResultLayer,
} from "@/utils/scene/fea/resultSelection";
// Mode-owned scene colouring: a shell reports mode transitions, core suspends
// and restores the active FEA field on a mode's declared behalf (issue #308).
export {
  notifyActiveModeSceneColor,
  sceneColorOwner,
} from "@/utils/scene/fea/modeSceneColor";
export type { SceneColorMode } from "@/utils/scene/fea/modeSceneColor";
export { buildFeaResultHierarchy } from "@/utils/scene/fea/resultHierarchy";
export { availableResultLayers } from "@/utils/scene/fea/resultLayers";
// Reading a picked element back out as numbers, using the same layer filter and
// IP reduction the colouring used.
export { feaValuesForElement, elementLabelNumber } from "@/utils/scene/fea/elementValues";
export type {
    ElementValuesResult,
    ElementFieldValues,
    ElementComponentValue,
    ElementValueScope,
} from "@/utils/scene/fea/elementValues";
export { selectedResultRange, selectedResultUnit } from "@/utils/scene/fea/resultUnits";
export type { FeaManifest, FeaManifestField } from "@/services/viewerApi";

// ---------------------------------------------------------------------------
// Camera, selection and visibility ops. Use these rather than touching the
// camera/controls refs directly: they carry the helper-exclusion and
// draw-range bookkeeping that hand-rolled equivalents get wrong.
// ---------------------------------------------------------------------------
export { center_on_bounding_box, centerViewOnSelection } from "@/utils/scene/centerViewOnSelection";
export { zoomToAll } from "@/components/viewer/sceneHelpers/setupCameraControlsHandlers";
export { hideSelectedRanges, unhideAllRanges } from "@/utils/scene/visibility";
export { selectInOtherModel } from "@/utils/scene/crossModelSelect";

// ---------------------------------------------------------------------------
// Reusable scene chrome. OPTIONAL: a shell is expected to build its own layout,
// but these two are tightly coupled to the scene (the outliner drives selection;
// the legend reads the active colour field), so re-implementing them is a
// reliable source of drift. Core's menus, panels and info boxes are deliberately
// NOT exported — a shell replaces those.
// ---------------------------------------------------------------------------
export { default as ResizableTreeView } from "@/components/tree_view/ResizableTreeView";
export { default as ColorLegend } from "@/components/viewer/ColorLegend";
// The external-model browser, with the store that toggles it.
//
// On the facade because a shell replaces the whole UI, so a shell that does not
// mount this has no external-model feature at all -- the admin tab can bind a
// scope and nothing can then load from it. Core mounts it from its own menu bar;
// a shell puts it wherever its chrome keeps "open something from elsewhere".
//
// The panel reads the binding and the provider itself and renders its own empty
// and unbound states, so a shell mounts it unconditionally and does not have to
// know whether the current scope is bound.
//
// The ICON is deliberately not exported. A shell names an icon from its own set
// for its own panels; the only way a core glyph helps is if a shell imports it
// into its panel registry at module scope, and importing anything from this
// entry point there drags the scene graph -- and the model worker -- into every
// test that touches the registry.
export { default as ExternalModelsPanel } from "@/components/ExternalModelsPanel";
export { useExternalModelsStore } from "@/state/externalModelsStore";
