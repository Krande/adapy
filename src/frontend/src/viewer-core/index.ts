// `@/viewer-core` — the stable surface a UI shell is allowed to depend on.
//
// WHY THIS EXISTS
// ---------------
// A UI shell (see `@/plugins/uiShells`) replaces the whole viewer UI. Without a
// named contract it would import whatever core file it happened to need, and
// every internal rename would become a breaking change for an out-of-tree UI
// repo we cannot grep. This facade turns that implicit, unbounded coupling into
// an explicit one: a shell imports ONLY from `@/viewer-core*`, and everything it
// depends on is listed here in one reviewable file.
//
// It is a CURATED RE-EXPORT BARREL, not a new abstraction layer. Nothing is
// wrapped, adapted or re-implemented — core's own modules are the
// implementation, and this file is the promise about which of them are public.
// That is deliberate: wrapping the viewer would mean refactoring exactly the
// files a UI rewrite is already churning, for no gain in swappability.
//
// THE ENTRY POINTS, split by dependency weight — so a canvas-less shell profile
// (a /convert or /admin page) does not drag the 3D + FEA code into its chunk,
// and a plugin that merely declares itself stays testable without a DOM:
//
//   `@/viewer-core`          this file. DEPENDENCY-FREE: the contract version,
//                            plugin declaration + every slot type, the
//                            UI-shell registry APIs, and the panel registry
//                            and panel intents. Imports no DOM, no store,
//                            no three — so `node --test` can import it, which is
//                            the same property `@/plugins/registry` is written
//                            for and the reason that rule is tested, not hoped.
//   `@/viewer-core/app`      providers, stores/refs, REST client, runtime
//                            config, scope, theme, file classification.
//   `@/viewer-core/scene`    the canvas, model loading, camera / selection /
//                            visibility ops, and the reusable scene chrome.
//   `@/viewer-core/plugins`  the slot HOSTS a shell mounts so OTHER plugins'
//                            panels appear in its chrome. Separate because the
//                            plugin context pulls in the FEA streaming module.
//   `@/viewer-core/layout`   core's panel contents (each panel without its
//                            classic frame), the shared panel chrome, and the
//                            layout host. Separate because the contents are
//                            the panels themselves: importing it is opting in
//                            to their weight.
//
// COMPATIBILITY
// -------------
// `VIEWER_CORE_API_VERSION` is bumped on a breaking change to anything exported
// from the entry points — the same contract `PLUGIN_API_VERSION` provides
// for the slot interfaces. A shell declares the range it was built against via
// its plugin's `coreApiRange`.
//
// RULES FOR THIS FILE
// -------------------
//  * Re-export from LEAF modules only. In particular never from `@/plugins`
//    (the loader barrel): it imports `registry.generated`, which imports the
//    plugin packages, which import this facade — an import cycle that would
//    bite at module-init time.
//  * Adding an export is a contract decision, not a convenience: everything
//    here is something an out-of-tree repo may pin to.
//  * `src/__tests__/plugins/viewerCoreFacade.test.ts` enforces both rules plus
//    the "shells import only the facade" fence.

// Also bumped on an ADDITIVE export a shell can DEPEND on, for the reason
// `PLUGIN_API_VERSION` is: without a version to name in `coreApiRange`, a shell
// built against the newer facade fails on an older core at an undefined import,
// from inside the shell, with nothing naming the mismatch.
//
//   1.4.0  `useComputeStore` + `computeJobOptions` on `@/viewer-core/app`:
//          the user's compute settings (cores, memory) for server-side jobs.
//          Plus set isolation on `@/viewer-core/scene` — `applyFeaGroupVisibility`
//          and the `feaSets` arithmetic — so a shell and a result plugin scope
//          the scene through one path.
//   1.3.0  `notifyActiveModeSceneColor` + `sceneColorOwner` on
//          `@/viewer-core/scene`, with `PluginModeSpec.ownsSceneColor`: core
//          suspends/restores the active FEA field for a mode that paints its
//          own scene colouring (issue #308). Plus `AdminPanel` on
//          `@/viewer-core/app`, so a shell mounts the admin surface rather
//          than forking it.
//   1.2.0  derived result hierarchy, component/layer actions and unit helpers
//          on `@/viewer-core/scene`.
//          Plus `CanvasWrapper`'s `legend` prop, so a shell that places the
//          legend itself can stop core mounting a second one, and
//          `feaValuesForElement` for reading a picked element as numbers.
//   1.1.0  `ExternalModelsPanel` + `useExternalModelsStore` on
//          `@/viewer-core/scene`. Both kept, DEPRECATED: the panel is gone
//          (the Sources tab replaces it) and the export now renders nothing.
//
// Out of sequence, because the list above runs newest-first and this is the
// newest entry:
//
//   1.5.0  `canOpenInScene` on `@/viewer-core/app`, and the
//          `RenderableFileProvider` slot types below. A shell that offers files
//          — a storage browser, a gallery — must ask that ONE predicate rather
//          than compose `isStreamingFEAResult || canLoadIntoSceneLegacy` for
//          itself, because the answer now also depends on what plugins have
//          registered and a shell composing it by hand would silently hide
//          their files. Kept in step with PLUGIN_API_VERSION 1.5.0, which is
//          the same capability seen from the plugin's side.
//   1.7.0  `localShutdownAvailable` + `stopLocalViewer` on `@/viewer-core/app`:
//          a shell offers "stop the local viewer" on a viewer a launcher
//          started with a shutdown token, and nowhere else. 1.6.0 is skipped
//          on purpose: PLUGIN_API_VERSION already is 1.6.0 for a different
//          capability, and a shell's `coreApiRange` is checked against THAT
//          number, so this entry takes the next one both can share.
//   1.8.0  Shared helpers a result plugin used to copy, on `@/viewer-core/app`:
//          `formatNumber`, the banded `ColorScale` (`scaleColor`, `cssScaleColor`),
//          `readSidecarElementFieldStep` / `readSidecarElementFieldEnvelope` (AFEL
//          blobs in a plugin's own sidecar, through core's reader),
//          `withCacheBust`, `registerJobFinisher`, the cross-window simulation
//          sync (`postSim`, `subscribeSim`, `followerParams`, `followerUrl`) and
//          `CollapsibleSection`. Ships with PLUGIN_API_VERSION
//          1.9.0 (the renderer-neutral scene overlays and `showColorScale`).
//   1.9.0  The `SceneBox` and `SceneFace` types of plugin API 1.10.0's view verbs
//          (`frameBox`, `findFaces` / `focusFace`).
//   1.10.0 The panel registry and the panel intents, here on the root entry:
//          `registerPanel` / `listPanels` / `getPanel` / `listToolbarPanels`,
//          `reveal` / `hide` / `toggle` / `isPanelOpen`, `bindStoreFlag`, and the
//          panel and layout types. One catalogue of every panel core and the
//          plugins offer, each bound to the visibility flag it already has, so a
//          shell lists panels and opens them by id instead of rediscovering them.
//          Additive. Ships with PLUGIN_API_VERSION 1.12.0.
//   1.11.0 The `@/viewer-core/layout` entry: core's panel CONTENTS, split from
//          their classic frames (`SceneBody`, `ObjectInfoBody`, `StorageBody`,
//          `OptionsBody`, `SimulationBody`, `CellBuilderBody`, ...), the shared
//          panel chrome (`PanelCard`, `PanelFrame`, `WindowFrame`,
//          `FloatingWindow`, `SheetHandle`, `useBottomSheet`, the named
//          breakpoints), and the layout host with its store and pure reducers.
//          On the root: `PanelSpec.content` (a panel's frameless content) and
//          `PanelSpec.watch` (when `available` may change), `watchStores`, and
//          `PanelFrameProps.dragHandleClassName`. Additive.
//   1.12.0 Undocking, on `@/viewer-core/layout`: the reducers `floatPanel`,
//          `setFloatRect`, `dockPanel`, `minimisePanel`, `restorePanel`; the
//          header-action slot of a panel's own frame (`PanelHeaderActions`,
//          `PanelHeaderActionsProvider`, `usePanelHeaderActions`) and its glyphs;
//          `localLayoutPersistence`; `FloatingWindow`'s caller-kept `rect` /
//          `onRectChange`. Additive.
//   1.13.0 Undocking, continued, on `@/viewer-core/layout`: the per-user server
//          backend `serverLayoutPersistence` (debounced, with a browser-local
//          fallback) and its `PreferenceStore`; the zone keys (`ZoneKeys`:
//          F6 / Shift+F6 between zones marked `ZONE_ATTR`, Esc on a floating
//          window, `useFloatFocus` / `restoreFocus`); an optional
//          `panelId` on `PanelHeaderActionsProvider` and `WindowHeaderActions`;
//          drag-to-dock (`moveDockDrag` / `endDockDrag` hit-testing drop targets
//          marked `DROP_ATTR`, `dockDragStore`) and `FloatingWindow`'s
//          `onDragMove` / `onDragEnd`; `OverlayPortal` / `TransformedHost`
//          (a fixed overlay of content in a floating window portals to the
//          body). On `viewerApi`: `getPreference` / `setPreference`.
//          Options on core's panel contents, so a shell uses them instead of a
//          copy. On `@/viewer-core/layout`: `SceneTreeBody` (the Scene tree) with
//          `SceneTreeProps` (`rootFilter`, `rootFilterLabels`, `extraRoots`, `onSelectOverride`,
//          `header`) and `TreeNodeData.meta`; `TreeBody`'s `sceneTree`;
//          `SceneBodyProps` (`tabs`, `hiddenTabs`, `femTab`, `arrangement`,
//          `loadedTitle`, `loadedEmpty`); `ObjectInfoBodyProps` (`onShowInData`)
//          and `CORE_PROPERTY_PROVIDERS`. On the root: `registerSceneTreeRoots` /
//          `listSceneTreeRoots`, the rows a plugin adds to the Scene tree, and
//          `registerPropertyProvider` / `listPropertyProviders` / `providersFor`,
//          the sections a plugin adds to the Selected Object panel.
//          Simulation: `SimulationBodyProps` (`layout: "panel" | "inline"`,
//          `showWarpToggle`, `showBeamSolids`, `showColorToggle`,
//          `excludeDockedTabs`: a dock named in `dock` or as `region`) and
//          `AnimationControls`. `ConvertBodyProps` (`inViewer`), and the progress
//          toast `ConversionProgress` with `isClaimed` (loads a host reports
//          elsewhere). The node editor's toolbar actions `request_list_of_nodes`
//          and `start_new_node_editor`.
//          The shared `CollapsibleSection` (its `"section"` and `"caret"`
//          looks), on `@/viewer-core/layout`. Additive.
//          Rearranging panels. On the root: `PanelAction.menu` (`PanelMenuItem`),
//          a header action that opens a menu. On `@/viewer-core/layout`:
//          `WindowHeaderMenuButton` and `MoveGlyph`; indexed drop targets
//          (`DROP_INDEX_ATTR`, `DROP_AXIS_ATTR`, `dropTargetAt`, `endDockDragAt`,
//          `DropTarget`, `DockDragState.index`); the zone kind `"dock"`;
//          `dragHandleClassName` on a docked `PanelFrame` marks the header a
//          host drags the panel by.
//          In-app dialogs on the root: `confirmDialog` / `alertDialog` /
//          `promptDialog` (promise-based; core's storage panel asks through them
//          instead of `window.confirm` / `alert` / `prompt`) and `setDialogHost`,
//          which installs a host's own.
//          Storage: `StorageBodyProps` (`rowLoadProgress`, `selectionActions:
//          "toolbar" | "rowMenu"`, `onConvert`, `onClose`, `scopePicker`,
//          `showModified: boolean | "auto"`) and `STORAGE_MODIFIED_MIN_PX`; its
//          `maximized` / `setMaximized` are kept, ignored and deprecated (the panel no
//          longer maximizes). On the root, the load claims the progress toast
//          consults by default: `claimLoad` / `releaseLoad` / `isLoadClaimed`,
//          `subscribeLoadClaims`, `loadClaimsVersion`.
//          Cellbuilder: `CellBuilderBodyProps` (`tabs`, `tabLabels`, `showUndoRedo`,
//          `showAddButtons`, `showComponentList`, `emptyState`), also taken by
//          `CellBuilderContent`, and `CellBuilderTab`. Additive.
//          The split grid of the classic preset, in place of a separate docked
//          preset: `LayoutDoc.grid` (`LAYOUT_DOC_VERSION` 2; an older document
//          starts over). On `@/viewer-core/layout`: the pure tree (`GridNode`:
//          `SplitNode`, `GroupNode`, `ViewportNode`; `dockInGrid`,
//          `removeFromGrid`, `setActiveTab`, `setSplitSizes`, `equaliseSplit`,
//          `resizePair`, `shownGrid`, `normaliseGrid`,
//          `gridGroups`, `gridPanels`, `groupOf`, `findNode`, `splitShare`,
//          `DEFAULT_GRID`, `VIEWPORT_NODE`, `ROOT_NODE`); the document reducers
//          `dockInGridDoc`, `updateGrid`, `gridOf`, `isInGrid`, `floatPanelAt`
//          (`floatPanel`, `dockPanel` and `closePanel` also take a panel out of
//          the grid); edge drop targets (`DROP_EDGES_ATTR`, `edgeAt`,
//          `DropTarget.edge`, `DockDragState.edge`, `cancelDockDrag`); a
//          panel's own window given actions and drag-to-dock by its host
//          (`WindowDockProvider`, `useWindowDock`, `WindowDockActions`,
//          `windowDockAttrs`; `FloatingWindow` and `WindowFrame` pick it up);
//          `FloatLayerProps.skip` and `windowDock`. `DOCKED_PRESET_ID` stays
//          a placement key only.
//          Composed shells: `UiShellSpec.load` is optional. Without it core
//          mounts its own root (routes, sign-in, viewer provider) and its layout
//          of floating and dockable panels for the shell, arranged by
//          `UiShellSpec.layout` (`UiShellLayout`: a starting `grid`, panels to
//          `open` when it loads), dressed by `chrome` (`UiShellChrome`:
//          a lazy `header`, `toolbar`) and `themeTokens`; each shell keeps its own
//          layout document (`LayoutDoc.context` is the shell id). `isComposedShell`;
//          `CORE_PANEL_IDS` / `CORE_PANEL_OWNER` / `CorePanelId` and the grid types
//          on the root. On `@/viewer-core/layout`: `PanelToggles` (the top bar's
//          toggles, for a shell's header), `LayoutHostProps.context`, and
//          `LayoutStoreState.setStartingDoc`.
//          Plugin panels in the layout: a slot that stands on its own (region
//          `top-panel`, or any slot that names a `dock`) has frameless content, so
//          it docks in the split grid and floats like a core panel; while the
//          layout shows it there, its region host leaves it out (its top-bar
//          button stays). `UiShellLayout.docks` maps a dock id to a group of the
//          shell's grid; a dock-naming plugin panel is placed there once.
export const VIEWER_CORE_API_VERSION = "1.13.0";

// ---------------------------------------------------------------------------
// Plugin declaration. `registerPlugin` is how a package announces itself —
// including a shell, via `uiShells`. The slot HOSTS live in
// `@/viewer-core/plugins` (heavier import).
// ---------------------------------------------------------------------------
export {
  disablePlugin,
  getPlugin,
  getPluginModes,
  getRegisteredPlugins,
  PLUGIN_API_VERSION,
  registerPlugin,
} from "@/plugins/registry";
export type {
  ActivationPredicate,
  AdaPluginContext,
  PanelSlot,
  PluginDockId,
  PluginModeId,
  PluginModeSpec,
  PluginApiClient,
  PluginLogLevel,
  PluginRegion,
  PluginSpec,
  PluginTheme,
  RegisteredPlugin,
  RenderableFileProvider,
  ResultSidecarLoader,
  SceneBox,
  SceneColor,
  SceneColorFieldProvider,
  SceneColorFieldResult,
  SceneElementOutline,
  SceneElementPaint,
  SceneFace,
  SceneHandle,
  SceneLabel,
  SceneLabelOptions,
  SceneLines,
  SceneOverlay,
  SidecarFetcher,
  TopBarButtonSpec,
  UrlParamHandler,
} from "@/plugins/registry";

// ---------------------------------------------------------------------------
// UI shells — registration and resolution. The switcher COMPONENT lives in
// `@/viewer-core/app`; this half is data, so it stays dependency-free.
// ---------------------------------------------------------------------------
export {
  activeUiShell,
  activeUiShellId,
  buildDefaultUiShellId,
  CORE_UI_SHELL_ID,
  getUiShell,
  listUiShells,
  setActiveUiShell,
  UI_SHELL_STORAGE_KEY,
  UI_SHELL_URL_PARAM,
} from "@/plugins/uiShells";
export type { RegisteredUiShell, UiShellChrome, UiShellLayout, UiShellResolution, UiShellSpec } from "@/plugins/uiShells";
export { isComposedShell } from "@/plugins/uiShells";
// Core's panel ids, for a composed shell's `layout` (dependency-free: the ids alone).
export { CORE_PANEL_IDS, CORE_PANEL_OWNER } from "@/layout/corePanelIds";
export type { CorePanelId } from "@/layout/corePanelIds";
export type { GridNode, GroupNode, SplitDir, SplitNode, ViewportNode } from "@/layout/splitTree";

// ---------------------------------------------------------------------------
// Panels (1.10.0) -- the catalogue of every panel, core's and plugins', and the
// intents that open and close one by id. Data and functions only, so it stays
// dependency-free; core's own panels are registered at bootstrap.
// ---------------------------------------------------------------------------
export {
  DEFAULT_PANEL_CAPABILITIES,
  placementFor,
} from "@/layout/panel";
export type {
  PanelAction,
  PanelMenuItem,
  PanelBinding,
  PanelBodyLoader,
  PanelCapabilities,
  PanelConstraints,
  PanelEnv,
  PanelFrameProps,
  PanelHostInfo,
  PanelId,
  PanelOwnerId,
  PanelPlacement,
  PanelPlacements,
  PanelProps,
  PanelSpec,
  PresetId,
  RegisteredPanel,
  ZoneId,
} from "@/layout/panel";
export {
  getPanel,
  isPanelAvailable,
  listPanels,
  listToolbarPanels,
  registerPanel,
} from "@/layout/registry";
export { hide, isPanelOpen, reveal, toggle } from "@/layout/reveal";
export type { PanelController } from "@/layout/reveal";
export {
  listSceneTreeRoots,
  registerSceneTreeRoots,
} from "@/plugins/sceneTreeRoots";
export type { SceneTreeRootProvider } from "@/plugins/sceneTreeRoots";
export {
  listPropertyProviders,
  providersFor,
  registerPropertyProvider,
} from "@/plugins/propertyProviders";
export type { PropertyProvider, SelectionSnapshot } from "@/plugins/propertyProviders";
export {
  alertDialog,
  confirmDialog,
  promptDialog,
  setDialogHost,
} from "@/plugins/dialogs";
export type {
  AlertOptions,
  ConfirmOptions,
  DialogHost,
  DialogText,
  DialogTone,
  PromptOptions,
} from "@/plugins/dialogs";
export {
  claimLoad,
  isLoadClaimed,
  loadClaimsVersion,
  releaseLoad,
  subscribeLoadClaims,
} from "@/plugins/loadClaims";
export { bindStoreFlag, watchStores } from "@/layout/bindings";
export type { FlagStore } from "@/layout/bindings";
export {
  CLASSIC_PRESET_ID,
  CLASSIC_ZONES,
  DOCKED_PRESET_ID,
  layoutDocKey,
} from "@/layout/layout";
export type {
  FloatState,
  LayoutDoc,
  LayoutPersistence,
  LayoutPrefs,
  LayoutPreset,
  ZoneDef,
  ZoneKind,
  ZoneState,
} from "@/layout/layout";
