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
export { STORAGE_MODIFIED_MIN_PX, StorageBody } from "@/components/storage/StorageBrowser";
export type { StorageBodyProps } from "@/components/storage/StorageBrowser";
export { OptionsBody } from "@/components/OptionsComponent";
export type { OptionsBodyProps, OptionsSectionId } from "@/components/OptionsComponent";
export type { SceneTreeRowOptions } from "@/components/tree_view/CustomNode";
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
export type { CellBuilderBodyProps, CellBuilderPanelState, CellBuilderTab } from "@/components/viewer/CellBuilderPanel";
export { ComponentControlsBody } from "@/components/component_view/ComponentControls";
export { default as NodeEditorBody } from "@/components/node_editor/NodeEditorBody";
// The node editor's toolbar actions, for a host that draws that toolbar itself.
export { request_list_of_nodes } from "@/utils/node_editor/handlers/request_list_of_nodes";
export { start_new_node_editor } from "@/utils/node_editor/handlers/start_new_node_editor";
export { default as TreeBody } from "@/components/asset_browser/AssetBrowser";
export type { TreeBodyProps } from "@/components/asset_browser/AssetBrowser";
export { default as SceneTreeBody } from "@/components/tree_view/TreeViewComponent";
export type { SceneTreeProps, SceneTreeRootFilter, SceneTreeRootFilterLabels } from "@/components/tree_view/TreeViewComponent";
export type { TreeNodeData } from "@/components/tree_view/CustomNode";
export { default as AttributeSearchBody } from "@/components/asset_browser/AttributeSearchPanel";
export { default as ConvertBody } from "@/components/convert/ConvertPage";
export type { ConvertBodyProps } from "@/components/convert/ConvertPage";
export { default as ConversionProgress } from "@/components/conversion/ConversionProgress";
export type { ConversionProgressProps, ToastAnchor } from "@/components/conversion/ConversionProgress";
export type { IsClaimed } from "@/components/conversion/claimedLoads";
export { ServerInfoBody } from "@/components/server_info/ServerInfoBox";
export { WebsocketStatusBody } from "@/components/WebsocketStatusMenu";

// ---------------------------------------------------------------------------
// Panel chrome, shared by core's panels and available to a shell's.
// ---------------------------------------------------------------------------
export {
  PANEL_CHROME,
  PANEL_SURFACE,
  GROUP_DIVIDER,
  GROUP_HEADER,
  PANEL_BODY_ATTR,
  PANEL_ID_ATTR,
  bodyPaletteCss,
  groupTabClass,
  panelBodyAttrs,
  panelBodyAttrsFor,
  WINDOW_CARD,
  WINDOW_HEADER,
  WINDOW_HEADER_BUTTON,
  panelClass,
  windowHeaderClass,
} from "@/components/ui/panelChrome";
export type { PanelCardVariant } from "@/components/ui/panelChrome";
// Core bodies drawn outside a panel (a dialog, a flyout) take the shell's body palette under this.
export { PanelBodyPalette } from "@/components/ui/PanelBodyPalette";
export type { PanelBodyPaletteProps } from "@/components/ui/PanelBodyPalette";
export {
  AddGlyph,
  PinGlyph,
  CloseGlyph,
  DockGlyph,
  ExternalGlyph,
  FloatGlyph,
  MinimiseGlyph,
  MoreGlyph,
  MoveGlyph,
  PanelCard,
  PanelFrame,
  PanelHeaderActions,
  PanelHeaderActionsProvider,
  usePanelHeaderActions,
  WindowDockActions,
  WindowDockProvider,
  WindowFrame,
  WindowHeaderActions,
  WindowHeaderButton,
  WindowHeaderMenuButton,
  useWindowDock,
  windowDockAttrs,
} from "@/components/ui/PanelFrame";
export type { PanelCardProps, WindowDock, WindowFrameProps, WindowHeaderButtonProps } from "@/components/ui/PanelFrame";
export { FloatingWindow, usePointerDrag } from "@/components/ui/FloatingWindow";
export type { FloatingWindowProps, RememberedRect } from "@/components/ui/FloatingWindow";
export { SheetHandle } from "@/components/ui/SheetHandle";
export { CollapsibleSection } from "@/components/ui/CollapsibleSection";
export type { CollapsibleSectionProps } from "@/components/ui/CollapsibleSection";
export { useBottomSheet } from "@/utils/useBottomSheet";
export type { BottomSheet, SheetGrab } from "@/utils/useBottomSheet";
export { useIsMobile, useIsNarrow, useIsWide, useMediaQuery } from "@/utils/useIsMobile";
export { BREAKPOINTS, COMPACT_QUERY, NARROW_QUERY, WIDE_QUERY } from "@/layout/breakpoints";
export { clampRect, defaultRect } from "@/assets/floatingWindow";
export type { Extent, WindowRect } from "@/assets/floatingWindow";
export { OverlayPortal, TransformedHost, useInTransformedHost } from "@/components/ui/OverlayPortal";

// ---------------------------------------------------------------------------
// The layout host, its store and its pure reducers.
// ---------------------------------------------------------------------------
export { LayoutHost, PanelMount, lazyPanelBody, useLayout, usePanelStates, zonePanels } from "@/layout/LayoutHost";
export { PanelToggles } from "@/layout/PanelToggles";
export { CommandPaletteHost, MenuBar, closeCommandPalette, offeredMenuItems, openCommandPalette } from "@/layout/CommandPalette";
export type { MenuBarProps, MenuDef, MenuItemDef } from "@/layout/CommandPalette";
export {
  deleteWorkspace,
  listWorkspaces,
  loadWorkspace,
  resetArrangement,
  saveWorkspace,
} from "@/layout/presets/classicPlace";
export { COMPACT_GROUP_PX } from "@/layout/presets/groupCompact";
export { ShellModeSwitcher, ShellModeToolbar, useShellMode } from "@/layout/ShellModeSwitcher";
export type { ShellModeSwitcherProps } from "@/layout/ShellModeSwitcher";
export type { PanelTogglesProps } from "@/layout/PanelToggles";
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
  LAYOUT_PREFERENCE_KEY,
  LAYOUT_SAVE_DEBOUNCE_MS,
  serverLayoutPersistence,
} from "@/layout/serverPersistence";
export type {
  PreferenceStore,
  ServerLayoutPersistence,
  ServerLayoutPersistenceOptions,
} from "@/layout/serverPersistence";
export {
  LAYOUT_DOC_VERSION,
  adoptNewPanels,
  closePanel,
  dockInGridDoc,
  dockPanel,
  emptyLayoutDoc,
  floatPanel,
  floatPanelAt,
  gridOf,
  isInGrid,
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
  updateGrid,
  whereOpen,
} from "@/layout/reducers";
export type { OpenTarget } from "@/layout/reducers";
export {
  PANEL_ACTIONS_ATTR,
  PANEL_TOGGLE_ATTR,
  ZONE_ATTR,
  ZONE_ORDER,
  ZONE_PANEL_ATTR,
  cycleZone,
  orderZones,
  zoneKeyIntent,
} from "@/layout/zoneKeys";
export type { ZoneKeyContext, ZoneKeyEvent, ZoneKeyIntent, ZoneKind } from "@/layout/zoneKeys";
export { ZoneKeys, focusZone, requestFloatFocus, restoreFocus, useFloatFocus } from "@/layout/zoneFocus";
export {
  DROP_ATTR,
  DROP_AXIS_ATTR,
  DROP_EDGES_ATTR,
  DROP_INDEX_ATTR,
  cancelDockDrag,
  dockDragStore,
  dropTargetAt,
  dropZoneAt,
  edgeAt,
  endDockDrag,
  endDockDragAt,
  moveDockDrag,
} from "@/layout/dockDrag";
export type { DockDragState, DropCandidate, DropEdge, DropTarget } from "@/layout/dockDrag";
export type { ZoneKeysProps } from "@/layout/zoneFocus";
export { panelStates, panelStatesKey, subscribePanels } from "@/layout/panelStates";
export type { PanelState } from "@/layout/panelStates";
export { LAYOUT_ENGINE_PARAM, LAYOUT_ENGINE_STORAGE_KEY, layoutEngineEnabled } from "@/layout/flag";
export {
  COLLAPSED_PX,
  clampFixed,
  setFixedPx,
  setGroupArrangement,
  setGroupCollapsed,
} from "@/layout/splitTree";
export {
  DEFAULT_GRID,
  ROOT_NODE,
  VIEWPORT_NODE,
  dockInGrid,
  equaliseSplit,
  findNode,
  gridGroups,
  gridPanels,
  groupOf,
  normaliseGrid,
  removeFromGrid,
  resizePair,
  setActiveTab,
  setSplitSizes,
  shownGrid,
  splitShare,
} from "@/layout/splitTree";
export type {
  DockEdge,
  GridNode,
  GridTarget,
  GroupNode,
  Rect,
  ShownNode,
  ShownSplit,
  SplitDir,
  SplitNode,
  ViewportNode,
} from "@/layout/splitTree";
