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
// ---------------------------------------------------------------------------
export { canLoadIntoSceneLegacy, isFEAResult, isStreamingFEAResult } from "@/utils/scene/fileKinds";

// ---------------------------------------------------------------------------
// Error containment. Shared boundary; `variant="fullscreen"` is the root card.
// ---------------------------------------------------------------------------
export { default as ErrorBoundary } from "@/components/common/ErrorBoundary";


// ---------------------------------------------------------------------------
// The UI-shell switcher. Renders nothing while only one shell is registered, so
// a shell can mount it unconditionally in its chrome.
// ---------------------------------------------------------------------------
export { UiShellSwitcher } from "@/plugins/UiShellSwitcher";
