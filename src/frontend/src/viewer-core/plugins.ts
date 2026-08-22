// `@/viewer-core/plugins` — the slot HOSTS a shell mounts so that OTHER
// plugins' contributions appear in its chrome.
//
// Only a shell needs these. A shell that omits them still works, but every
// feature plugin's panels and buttons become invisible in that UI — so a UI
// meant to replace the classic one should mount at least the `top-panel` and
// `fem-sidebar` regions.
//
// Separate entry point because the plugin context resolves the active FEA mesh,
// which pulls the FEA streaming module — see the weight note in `@/viewer-core`.
//
// Declaring a plugin (`registerPlugin`) lives in `@/viewer-core`; this is the
// other direction: rendering what others declared.

export { PluginPanelRegion, PluginTopBarButtons } from "@/plugins/PluginSlots";
export { PluginColorFields } from "@/plugins/PluginColorFields";
export { makePluginContext, makePluginContextStandalone } from "@/plugins/context";
export { usePluginUiStore } from "@/plugins/pluginUiStore";
export {
  findSimulationTabById,
  getPanelsForRegion,
  getResultSidecarLoaders,
  getSceneColorFieldProviders,
  getSimulationTabs,
  getUrlParamHandlers,
} from "@/plugins/registry";
export type { SimulationTabEntry } from "@/plugins/registry";
export {
  disposeResultSidecarLoaders,
  makeManifestFetcher,
  runResultSidecarLoaders,
} from "@/plugins/sidecarLoaders";
export { dispatchPluginUrlParams } from "@/plugins/urlParams";
