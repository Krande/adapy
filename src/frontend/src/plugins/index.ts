// Public entry for the plugin system. `loadPlugins()` registers every built-in
// (build-time) plugin exactly once; call it at app bootstrap before the viewer
// UI mounts so the slot hosts see a populated registry on first render.

import { registerCoreUiShell } from "./coreUiShell";
import { DEFAULT_UI_SHELL, registerBuiltinPlugins } from "./registry.generated";
import { setBuildDefaultUiShellId } from "./uiShells";
import { usePluginUiStore } from "./pluginUiStore";
import { registerCorePanels } from "@/layout/corePanels";
import { registerDesktopUiShell } from "@/layout/desktop/desktopShell";
import { setPluginPanelVisibility } from "@/layout/pluginPanels";
import { makePluginContextStandalone } from "./context";
import { setPluginContextFactory } from "./registry";

let _loaded = false;

export function loadPlugins(): void {
  if (_loaded) return;
  _loaded = true;
  // The built-in UI shell registers FIRST and outside the try/catch: it is the
  // fallback every other shell falls back to, so it must exist even if a plugin
  // registration below blows up.
  registerCoreUiShell();
  // Core's second UI, the Desktop one (a composed shell): in every build, never the default.
  try {
    registerDesktopUiShell();
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error("[plugins] registerDesktopUiShell failed", err);
  }
  // Core's panel catalogue, then the store plugin panels with a top-bar button
  // keep their visibility in, so the panel registry binds plugin panels to it as
  // they register below. Neither changes what renders.
  setPluginPanelVisibility(usePluginUiStore);
  // ...and a plugin context, so a dormant plugin's panels count as unavailable.
  setPluginContextFactory(makePluginContextStandalone);
  try {
    registerCorePanels();
  } catch (err) {
    // The catalogue is not on the render path; the viewer must boot without it.
    // eslint-disable-next-line no-console
    console.error("[layout] registerCorePanels failed", err);
  }
  try {
    registerBuiltinPlugins();
  } catch (err) {
    // A broken plugin registration must never keep the viewer from booting.
    // eslint-disable-next-line no-console
    console.error("[plugins] loadPlugins failed", err);
  }
  // A page that asks for it before the viewer starts (a browser test's init script) is handed the
  // scene verbs plugins draw with, to script what no plugin of this build draws.
  const page = globalThis as { __adaScriptedScene?: unknown };
  if (page.__adaScriptedScene === true) {
    page.__adaScriptedScene = makePluginContextStandalone("scripted").scene;
  }
  // Build-time default UI (ADA_UI_DEFAULT / plugins.json `defaultUi`), applied
  // after registration so an unknown id can be reported against the real set.
  setBuildDefaultUiShellId(DEFAULT_UI_SHELL);
}

export * from "./registry";
export * from "./uiShells";
export { PluginPanelRegion, PluginTopBarButtons } from "./PluginSlots";
export { PluginColorFields } from "./PluginColorFields";
export { usePluginUiStore } from "./pluginUiStore";
export { runResultSidecarLoaders, disposeResultSidecarLoaders, makeManifestFetcher } from "./sidecarLoaders";
export { dispatchPluginUrlParams } from "./urlParams";
export { makePluginContext, makePluginContextStandalone } from "./context";
export { registerCoreUiShell } from "./coreUiShell";
export { DESKTOP_UI_SHELL_ID, registerDesktopUiShell } from "@/layout/desktop/desktopShell";
export { default as UiShellHost } from "./UiShellHost";
export { UiShellSwitcher } from "./UiShellSwitcher";
