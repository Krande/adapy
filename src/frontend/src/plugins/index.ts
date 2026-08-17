// Public entry for the plugin system. `loadPlugins()` registers every built-in
// (build-time) plugin exactly once; call it at app bootstrap before the viewer
// UI mounts so the slot hosts see a populated registry on first render.

import { registerBuiltinPlugins } from "./registry.generated";

let _loaded = false;

export function loadPlugins(): void {
  if (_loaded) return;
  _loaded = true;
  try {
    registerBuiltinPlugins();
  } catch (err) {
    // A broken plugin registration must never keep the viewer from booting.
    // eslint-disable-next-line no-console
    console.error("[plugins] loadPlugins failed", err);
  }
}

export * from "./registry";
export { PluginPanelRegion, PluginTopBarButtons } from "./PluginSlots";
export { PluginColorFields } from "./PluginColorFields";
export { usePluginUiStore } from "./pluginUiStore";
export { runResultSidecarLoaders, disposeResultSidecarLoaders, makeManifestFetcher } from "./sidecarLoaders";
export { dispatchPluginUrlParams } from "./urlParams";
export { makePluginContext, makePluginContextStandalone } from "./context";
