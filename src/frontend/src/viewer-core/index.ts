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
//                            plugin declaration + every slot type, and the
//                            UI-shell registry APIs. Imports no DOM, no store,
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
//
// COMPATIBILITY
// -------------
// `VIEWER_CORE_API_VERSION` is bumped on a breaking change to anything exported
// from the three entry points — the same contract `PLUGIN_API_VERSION` provides
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
//   1.3.0  `notifyActiveModeSceneColor` + `sceneColorOwner` on
//          `@/viewer-core/scene`, with `PluginModeSpec.ownsSceneColor`: core
//          suspends/restores the active FEA field for a mode that paints its
//          own scene colouring (issue #308).
//   1.2.0  derived result hierarchy, component/layer actions and unit helpers
//          on `@/viewer-core/scene`.
//          Plus `CanvasWrapper`'s `legend` prop, so a shell that places the
//          legend itself can stop core mounting a second one, and
//          `feaValuesForElement` for reading a picked element as numbers.
//   1.1.0  `ExternalModelsPanel` + `useExternalModelsStore` on
//          `@/viewer-core/scene`
export const VIEWER_CORE_API_VERSION = "1.3.0";

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
  ResultSidecarLoader,
  SceneColorFieldProvider,
  SceneColorFieldResult,
  SceneHandle,
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
export type { RegisteredUiShell, UiShellResolution, UiShellSpec } from "@/plugins/uiShells";
