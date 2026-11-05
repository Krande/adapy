// `@/viewer-core/layout` — core's panels as content a shell can frame itself, the
// shared panel chrome, and the layout host.
//
// Each core panel is published here WITHOUT its classic frame: the content the
// classic UI draws inside its menu-column card, bottom sheet or window. A shell
// mounts these in its own docks and frames, and gets every fix and feature of the
// panel with them, instead of carrying a copy. The same contents are reachable
// by id through the panel registry (`PanelSpec.content`, from `@/viewer-core`).
//
// Separate from `/app` by WEIGHT: importing this entry imports the panels.
// Everything here assumes an enclosing `<AdaViewerProvider>`.
//
// See `@/viewer-core` for the contract rules (leaf re-exports only, no
// `@/plugins` barrel, additions are contract decisions).

// ---------------------------------------------------------------------------
// Panel contents. Each ignores the host's `PanelProps`, so it can be registered
// as a panel body as it is.
// ---------------------------------------------------------------------------
export { default as SceneBody } from "@/components/info_box_scene/SceneBody";
export type { SceneArrangement, SceneBodyProps } from "@/components/info_box_scene/SceneBody";
export type { SceneTab } from "@/components/info_box_scene/sceneTabs";
export { CORE_PROPERTY_PROVIDERS, ObjectInfoBody } from "@/components/info_box_selected_object/ObjectInfoBoxComponent";
export type { ObjectInfoBodyProps } from "@/components/info_box_selected_object/ObjectInfoBoxComponent";
export { StorageBody } from "@/components/storage/StorageBrowser";
export type { StorageBodyProps } from "@/components/storage/StorageBrowser";
export { OptionsBody } from "@/components/OptionsComponent";
export {
  AnimationControls,
  SimulationBody,
  SimulationContent,
  useSimulationTabs,
} from "@/components/simulation/SimulationControls";
export type {
  ResultControlsOptions,
  SimulationBodyProps,
  SimulationTabs,
} from "@/components/simulation/SimulationControls";
export { default as FeaTableBody } from "@/components/simulation/SimulationDataInfoPanel";
export {
  CellBuilderBody,
  CellBuilderContent,
  useCellBuilderPanelState,
} from "@/components/viewer/CellBuilderPanel";
export type { CellBuilderPanelState } from "@/components/viewer/CellBuilderPanel";
export { ComponentControlsBody } from "@/components/component_view/ComponentControls";
export { default as NodeEditorBody } from "@/components/node_editor/NodeEditorBody";
export { default as TreeBody } from "@/components/asset_browser/AssetBrowser";
export type { TreeBodyProps } from "@/components/asset_browser/AssetBrowser";
export { default as SceneTreeBody } from "@/components/tree_view/TreeViewComponent";
export type { SceneTreeProps, SceneTreeRootFilter } from "@/components/tree_view/TreeViewComponent";
export type { TreeNodeData } from "@/components/tree_view/CustomNode";
export { default as AttributeSearchBody } from "@/components/asset_browser/AttributeSearchPanel";
export { default as ConvertBody } from "@/components/convert/ConvertPage";
export { ServerInfoBody } from "@/components/server_info/ServerInfoBox";
export { WebsocketStatusBody } from "@/components/WebsocketStatusMenu";

// ---------------------------------------------------------------------------
// Panel chrome, shared by core's panels and available to a shell's.
// ---------------------------------------------------------------------------
export {
  PANEL_CHROME,
  PANEL_SURFACE,
  WINDOW_CARD,
  WINDOW_HEADER,
  WINDOW_HEADER_BUTTON,
  panelClass,
  windowHeaderClass,
} from "@/components/ui/panelChrome";
export type { PanelCardVariant } from "@/components/ui/panelChrome";
export {
  CloseGlyph,
  DockGlyph,
  ExternalGlyph,
  FloatGlyph,
  MinimiseGlyph,
  PanelCard,
  PanelFrame,
  PanelHeaderActions,
  PanelHeaderActionsProvider,
  usePanelHeaderActions,
  WindowFrame,
  WindowHeaderActions,
  WindowHeaderButton,
} from "@/components/ui/PanelFrame";
export type { PanelCardProps, WindowFrameProps, WindowHeaderButtonProps } from "@/components/ui/PanelFrame";
export { FloatingWindow, usePointerDrag } from "@/components/ui/FloatingWindow";
export type { FloatingWindowProps, RememberedRect } from "@/components/ui/FloatingWindow";
export { SheetHandle } from "@/components/ui/SheetHandle";
export { useBottomSheet } from "@/utils/useBottomSheet";
export type { BottomSheet, SheetGrab } from "@/utils/useBottomSheet";
export { useIsMobile, useIsNarrow, useIsWide, useMediaQuery } from "@/utils/useIsMobile";
export { BREAKPOINTS, COMPACT_QUERY, NARROW_QUERY, WIDE_QUERY } from "@/layout/breakpoints";
export { clampRect, defaultRect } from "@/assets/floatingWindow";
export type { Extent, WindowRect } from "@/assets/floatingWindow";

// ---------------------------------------------------------------------------
// The layout host, its store and its pure reducers.
// ---------------------------------------------------------------------------
export { LayoutHost, PanelMount, lazyPanelBody, useLayout, usePanelStates, zonePanels } from "@/layout/LayoutHost";
export type { LayoutContextValue, LayoutHostProps } from "@/layout/LayoutHost";
export { DefaultWindowChrome, FloatLayer } from "@/layout/FloatLayer";
export type { FloatLayerProps, WindowChrome } from "@/layout/FloatLayer";
export { createLayoutController } from "@/layout/controller";
export {
  createLayoutStore,
  layoutStore,
  memoryLayoutPersistence,
  migrateLayoutPrefs,
} from "@/layout/layoutStore";
export type { LayoutStoreState } from "@/layout/layoutStore";
export { LAYOUT_PREFS_STORAGE_KEY, localLayoutPersistence } from "@/layout/localPersistence";
export type { PrefsStorage } from "@/layout/localPersistence";
export {
  LAYOUT_DOC_VERSION,
  adoptNewPanels,
  closePanel,
  dockPanel,
  emptyLayoutDoc,
  floatPanel,
  isOpenIn,
  migrateLayoutDoc,
  minimisePanel,
  movePanel,
  openPanel,
  raiseFloat,
  restorePanel,
  setFloatRect,
  setZoneCollapsed,
  setZoneSize,
  whereOpen,
} from "@/layout/reducers";
export type { OpenTarget } from "@/layout/reducers";
export { panelStates, panelStatesKey, subscribePanels } from "@/layout/panelStates";
export type { PanelState } from "@/layout/panelStates";
export { LAYOUT_ENGINE_PARAM, LAYOUT_ENGINE_STORAGE_KEY, layoutEngineEnabled } from "@/layout/flag";
