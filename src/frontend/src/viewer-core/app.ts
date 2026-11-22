// `@/viewer-core/app` — the running viewer's substrate: providers, stores, the
// REST client, runtime config, scope and theme.
//
// Split from `@/viewer-core` (which is dependency-free by construction) because
// everything here touches the DOM or creates stores at module init. A plugin
// that only DECLARES itself imports the root and stays headless-testable; a
// shell, which actually mounts a viewer, imports this.
//
// See `@/viewer-core` for the contract rules.

// ---------------------------------------------------------------------------
// Mounting + per-instance state
//
// `AdaViewerProvider` is the root every shell mounts: it supplies the refs
// (scene/camera/controls/renderer) and the zustand store bag. A shell that
// renders the canvas without it will throw from `useAdaViewerCtx`.
// ---------------------------------------------------------------------------
export {
  AdaViewerProvider,
  getSingletonViewerStores,
  useAdaViewerCtx,
  useViewerRefs,
  useViewerStores,
} from "@/state/AdaViewerContext";
export type { AdaViewerCtx, AdaViewerRefs, AdaViewerStores } from "@/state/AdaViewerContext";
// Or, instead of mounting the provider by hand: core's own root, with the
// canvas-less routes, sign-in and deep links that every shell core mounts gets.
export { AppRoot } from "@/layout/SharedAppRoot";
// Switching scope, as core's scope pickers do: `requestScopeChange` asks first when it
// would unload models; `applyScopeChange` switches at once; `sceneHasModels`.
export { applyScopeChange, requestScopeChange, sceneHasModels } from "@/utils/scope/scopeChange";
// The Connections panel opened on one of its tabs: core's, or a plugin's `connection-builder` panel
// by its namespaced id (plugin API 1.29.0). It picks the tab in the panel's tab store.
export { openConnectionsTab } from "@/layout/connectionsTabActions";

// ---------------------------------------------------------------------------
// Deployment mode + build info (REST vs websocket, Jupyter, embed flags).
// ---------------------------------------------------------------------------
export { runtime } from "@/runtime/config";
export type { Runtime } from "@/runtime/config";

// ---------------------------------------------------------------------------
// Backend. The single REST client — a shell must not hand-roll fetches against
// the viewer API, or it will miss auth headers and scope handling.
// ---------------------------------------------------------------------------
export { viewerApi } from "@/services/viewerApi";
// Simulation providers and runs (1.17.0): the client, and the wire shapes the
// `simulationProviders` slot's callbacks are handed.
export { simulationsApi } from "@/services/api/simulations";
export type {
  ArtefactBytes,
  ArtefactRange,
  ListRunsOptions,
  SimulationArtefact,
  SimulationBatch,
  SimulationBatchRef,
  SimulationBatchesResponse,
  SimulationDomain,
  SimulationProviderInfo,
  SimulationRun,
  SimulationRunsPage,
  SimulationSubjectRef,
  StartSimulationBody,
} from "@/services/api/simulations";
// Robot-asset bundles (1.37.0): the provider store's robot assets exported to and imported from one
// `.adabots` file. Admin only.
export { robotBundlesApi, saveBundle } from "@/services/api/robotBundles";
export type { RobotBundleDownload, RobotBundleSelection } from "@/services/api/robotBundles";
export { ROBOT_BUNDLE_SCHEMA, ROBOT_BUNDLE_SUFFIX } from "@/assets/robotBundles";
export type {
  RobotAssetGrant,
  RobotAssetList,
  RobotAssetRow,
  RobotAssetVersion,
  RobotBundleLimits,
  RobotImportChoices,
  RobotImportPlanRow,
  RobotImportPreview,
  RobotImportResult,
  RobotImportResultKind,
  RobotImportResultRow,
  RobotImportStatus,
} from "@/assets/robotBundles";
// Per-element associations (1.36.0): the contacts, connections, welds, subjects and simulation runs
// filed under a model's elements and their ancestors, a page at a time; the hook follows a run that
// is going.
export { elementLinks, elementLinksApi, latestRun, queryElementLinks } from "@/services/api/elementLinks";
export type {
  ConnectionLinks,
  ElementLink,
  ElementLinkGroup,
  ElementLinkKind,
  ElementLinksPage,
  ElementLinksQuery,
  ElementLinksSummary,
  ElementNodeRef,
  MakeSubjectsResult,
  SimulationBatchPolicy,
  StartElementsBatchBody,
  StartElementsBatchResult,
} from "@/services/api/elementLinks";
export { ELEMENT_LINKS_REFRESH_MS, useElementLinks } from "@/hooks/useElementLinks";
export type { ElementLinksState, UseElementLinksOptions } from "@/hooks/useElementLinks";
// What a run can be started ON (1.21.0): core chrome that has no plugin context offers a subject
// through the store; a plugin uses `ctx.simulations.offerSubject`.
export { useSimulationSubjectStore } from "@/state/simulationSubjects";
export type { SimulationSubject } from "@/state/simulationSubjects";

// ---------------------------------------------------------------------------
// Finishing a tracked job: register, at module load, what to do once a job of a
// `kind` (AdaPluginContext.trackJob) is done -- also for a job resumed after a reload.
// ---------------------------------------------------------------------------
export { registerJobFinisher } from "@/services/jobTracking";
export type { JobFinishContext, JobFinisher, JobFinishResult } from "@/services/jobTracking";

// ---------------------------------------------------------------------------
// Cross-window simulation sync (BroadcastChannel): a maximized / follower window
// of a Simulation panel drives the original tab's scene, and back.
// ---------------------------------------------------------------------------
export { followerParams, followerUrl, postSim, subscribeSim } from "@/utils/simChannel";
export type { SimMsg } from "@/utils/simChannel";

// ---------------------------------------------------------------------------
// Panel building blocks a plugin panel shares with core's own.
// ---------------------------------------------------------------------------
export { default as CollapsibleSection } from "@/components/common/CollapsibleSection";

// ---------------------------------------------------------------------------
// Number formatting and banded colour scales (pure).
// ---------------------------------------------------------------------------
export { formatNumber } from "@/utils/format";
export type { FormatNumberOptions } from "@/utils/format";
export { cssScaleColor, scaleColor } from "@/utils/colorScale";
export type { ColorScale, ColorScaleBand, ScaleColor } from "@/utils/colorScale";

// ---------------------------------------------------------------------------
// A plugin's own sidecar: AFEL element-field blobs read with core's reader (range
// fetch, whole-blob fallback, header checks, caching), and cache-busting for a
// sidecar a re-run rewrites in place.
// ---------------------------------------------------------------------------
export {
  readSidecarElementFieldEnvelope,
  readSidecarElementFieldStep,
  withCacheBust,
} from "@/services/sidecarElementField";
export type { ElementFieldEnvelope, SidecarElementFieldBlob } from "@/services/sidecarElementField";

// ---------------------------------------------------------------------------
// The local viewer's lifetime. On a viewer a launcher started on a workstation
// (and only there), the page may stop it: `localShutdownAvailable()` says
// whether to offer that, `stopLocalViewer()` does it. Never available on a
// deployment. See `@/services/localViewer`.
// ---------------------------------------------------------------------------
export { localShutdownAvailable, stopLocalViewer } from "@/services/localViewer";
export type { StopLocalViewerResult } from "@/services/localViewer";

// ---------------------------------------------------------------------------
// Active scope (user / project namespace every storage call is relative to).
// ---------------------------------------------------------------------------
export { scopeFromUrlPart, scopeUrlPart, useScopeStore } from "@/state/scopeStore";
export type { ScopeOption } from "@/state/scopeStore";

// ---------------------------------------------------------------------------
// Render cadence. The viewer renders on demand — anything that changes what the
// scene should look like must call `requestRender()` or the frame never repaints.
// ---------------------------------------------------------------------------
export { requestRender, usePerfStore } from "@/state/perfStore";

// ---------------------------------------------------------------------------
// Compute settings. What a server-side calculation may spend — cores and
// memory — as the user sets it. A shell renders the controls; a plugin sends
// `computeJobOptions()` with its job, and the worker's environment caps it.
// ---------------------------------------------------------------------------
export {
  COMPUTE_MODES,
  computeJobOptions,
  computeJobOptionsFrom,
  isComputeMode,
  useComputeStore,
} from "@/state/computeStore";
export type { ComputeMode, ComputeState } from "@/state/computeStore";

// ---------------------------------------------------------------------------
// Theme. A shell may ship its own design system, but reading these keeps it in
// step with the user's panel-chrome choice and with plugin panels, which paint
// from `effectivePluginTheme`.
// ---------------------------------------------------------------------------
export {
  effectivePanelTheme,
  effectivePluginTheme,
  PANEL_CHROME,
  SEMANTIC_TOKENS,
  THEME_PRESETS,
  // The subscribing counterpart of `effectivePluginTheme`. A shell or panel
  // that paints from the token OBJECT needs this to repaint on a theme
  // switch; one that paints from the `--ada-*` CSS variables does not.
  usePluginTheme,
  useThemeStore,
} from "@/state/themeStore";
export type { PanelTheme, ThemePresetId } from "@/state/themeStore";

// ---------------------------------------------------------------------------
// File classification. What a filename means (FEA result, streaming FEA result,
// loadable geometry) is core's call — a shell that re-derives it will drift.
//
// `canOpenInScene` is the one to reach for: "can this file go in the scene at
// all", across core's streaming bake, core's convert pipeline AND any plugin
// that registered a renderable-file provider. The other three are the
// individual routes, and a shell that ORs two of them together to answer the
// general question will hide every plugin-rendered file — which is exactly the
// drift this comment warns about, so it is spelled out.
// ---------------------------------------------------------------------------
export {
  canLoadIntoSceneLegacy,
  canOpenInScene,
  isFEAResult,
  isStreamingFEAResult,
} from "@/utils/scene/fileKinds";

// ---------------------------------------------------------------------------
// Error containment. Shared boundary; `variant="fullscreen"` is the root card.
// ---------------------------------------------------------------------------
export { default as ErrorBoundary } from "@/components/common/ErrorBoundary";


// ---------------------------------------------------------------------------
// The UI-shell switcher. Renders nothing while only one shell is registered, so
// a shell can mount it unconditionally in its chrome.
// ---------------------------------------------------------------------------
export { UiShellSwitcher } from "@/plugins/UiShellSwitcher";

// DEPRECATED, kept so a shell built against an earlier contract still resolves
// it: the admin tab that bound a scope to one external-model collection.
//
// The tab is gone. Nothing read the binding any more -- the provider system's
// per-scope collections (Admin -> Providers) replaced it -- and its one live
// part, the upstream-mirror controls, moved to the Providers tab.
// `ExternalModelsTab` renders nothing, and a shell should drop its mount.
export function ExternalModelsTab(): null {
  return null;
}

// The whole admin surface, for the same reason as the tab above but at the
// panel level: the one existing shell forked the entire panel, and upstream's
// tab consolidation (audit sub-tabs, performance, procedural) left the fork
// listing tabs core had retired — visible only as a typecheck error. Mounting
// this instead cannot drift. `embedded` mode exists for exactly the floating
// in-viewer host a shell would put it in; the full-page `/admin` route works
// unchanged.
export { default as AdminPanel } from "@/components/admin/AdminPanel";

// Scene > Overlays: core's overlays (systems, flow direction, penetrations, connections) drawn
// over the loaded models from the relations providers publish. Mounted as a whole for the reason
// above: the overlays are core entities, and a shell that forked the panel would drift from the
// set core defines.
export { default as OverlaysPanel } from "@/components/info_box_scene/OverlaysPanel";

// Systems and related elements, from the same relations index. `SystemsPanel` lists the loaded
// collections' systems (select what is loaded, load the rest); `RelatedSection` is the selected
// element's system, run and actions, for a shell's selection panel. A shell with a menu of its own
// lists the same actions through `useRelatedActions` -- the set is core's either way.
export { default as SystemsPanel } from "@/components/asset_browser/SystemsTab";
export { default as RelatedSection } from "@/components/info_box_selected_object/RelatedSection";
export { useRelatedActions } from "@/hooks/useRelatedActions";
export type { RelatedAction } from "@/utils/systems/relatedActions";

// Field help (1.31.0): what a builder doc's column means, as the engine catalog serves it (adapy's
// descriptions under the active engine's `field_help`, the engine winning per column), by doc list
// and column: `fieldHelp("spaces", "GRID_X_CREATE")`; `useFieldHelp` follows the catalog and the
// engine picked. The pure `engineFieldHelp(engines, slug)` / `fieldHelpText(help, list, column)` too.
export { engineFieldHelp, fieldHelp, fieldHelpText, useFieldHelp } from "@/state/cellbuilder/fieldHelp";
export type { FieldHelp } from "@/state/cellbuilder/fieldHelp";

// Builder refs (1.31.0): a plugin's or a shell's rows shown as boxes among the cell builder's
// cells, picked, moved and resized like them, each edit handed back to the provider. Returns the
// unregister. A plugin has the same through `ctx.scene.registerBuilderRefs`.
export { builderRefId, registerBuilderRefs } from "@/plugins/builderRefs";

// When a builder compile fails (1.31.0): `selectCompileFailure(state)` is the failed job (null
// otherwise), `onCompileFailed(listener)` is told once per failure -- for a shell that shows the
// compile log (`CompileLogView`, on /layout) in a drawer it opens when a compile fails.
export { onCompileFailed, selectCompileFailure } from "@/state/cellbuilder/compileFailures";
export type { CompileFailure } from "@/state/cellbuilder/compileFailures";

// Before a builder commit (1.31.0): `registerBeforeCommit(owner, hook)` runs `hook({doc, docExtras})`
// before every commit of the procedural model (the toolbar's, Ctrl+S, the one before a compile or
// an export); a hook that answers false vetoes it (a shell's own validation, say). Returns the
// unregister.
export { registerBeforeCommit } from "@/state/cellbuilder/beforeCommit";
export type { BeforeCommitContext, BeforeCommitHook } from "@/state/cellbuilder/beforeCommit";
