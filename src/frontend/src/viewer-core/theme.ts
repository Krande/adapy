// `@/viewer-core/theme` -- the panel theme, on an entry point that is safe to import anywhere.
//
// `@/viewer-core/app` is a barrel of the running viewer's substrate: importing it creates stores and
// reads `window` at module load, so a headless test or a module that only wants the theme cannot take
// it. This entry re-exports the theme alone, from its own module (stores and pure rules; nothing is
// read from the document until a theme is applied). Same names as on `/app`, which keeps them too.
//
// See `@/viewer-core` for the contract rules (leaf re-exports only).

export {
  effectivePanelTheme,
  effectivePluginTheme,
  PANEL_CHROME,
  SEMANTIC_TOKENS,
  THEME_PRESETS,
  usePluginTheme,
  useThemeStore,
} from "@/state/themeStore";
export type { PanelTheme, ThemePresetId } from "@/state/themeStore";
