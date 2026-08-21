// Derives the full runtime colour-token set from the user's panel theme.
//
// DEPENDENCY-FREE ON PURPOSE — no React, no three, no zustand, and above all no DOM.
// `themeStore` applies what this returns; this module only computes it. That split
// exists for two reasons:
//
//   1. It is unit-testable under plain `node --test` with no jsdom (same reasoning as
//      plugins/registry.ts).
//   2. It keeps the *application* imperative. themeStore writes these onto an element's
//      inline style rather than declaring them in a stylesheet, and that is load-bearing:
//      the embed build wraps its CSS in `@scope (.ada-viewer-scope)`, inside which a
//      `:root {}` rule matches nothing (<html> is not a descendant of the scope root).
//      A "tidy-up" that moves these into tokens.css silently unstyles the embed.
//      Static, theme-independent tokens (radii, spacing, type) DO live in tokens.css —
//      they have no such dependency.
//
// Derived values use CSS color-mix() rather than parsing the theme's colour strings.
// Preset colours are rgba() with meaningful alpha; re-deriving them numerically would
// mean reimplementing alpha compositing, and getting it subtly wrong. color-mix lets
// the browser do it, and works for any CSS colour the user's custom swatch produces.

import type {PanelTheme} from "@/state/themeStore";
import {SELECTION_COLOR} from "./selectionColor";

/** Theme-neutral status colours. Mid-tones chosen to read on light and dark presets. */
export const SEMANTIC_COLORS = {
    accent: "#3b82f6",
    pass: "#22c55e",
    warn: "#f59e0b",
    fail: "#ef4444",
    info: "#38bdf8",
} as const;

/**
 * Whether a theme is light-on-dark or dark-on-light, judged from its text colour.
 *
 * The panel background is unreliable for this — presets use rgba() with alpha as low
 * as 0.5, so a "light" panel over the dark 3D viewport composites to something dark.
 * The text colour is always fully opaque and always chosen to contrast with the
 * panel, which makes it the honest signal. `mist` is the one light preset.
 */
export function isDarkTheme(theme: Pick<PanelTheme, "text">): boolean {
    const hex = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(theme.text.trim());
    if (!hex) return true; // non-hex custom text: assume dark, the common case
    let h = hex[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    const n = parseInt(h, 16);
    // Rec. 601 luma — cheap and more than accurate enough for a binary decision.
    const luma = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
    return luma > 0.5; // light TEXT means a dark theme
}

/** Solid app-chrome background per direction. Opaque by design: it sits behind the
 *  3D canvas and the docks, and a translucent value there would composite against
 *  the browser's own backdrop. */
const APP_BG = {dark: "#0f1319", light: "#eef1f5"} as const;

const mix = (a: string, b: string, pct: number) => `color-mix(in srgb, ${a} ${pct}%, ${b})`;

/**
 * The complete `--ada-*` runtime colour set.
 *
 * Returns a plain map of CSS custom-property name → value. The nine names that
 * existed before the design system (`--ada-panel-*`, `--ada-accent|pass|warn|fail`)
 * are emitted byte-identically: panels, plugin code and `PANEL_CHROME` all still
 * consume them, and `AdaPluginContext.theme` mirrors them.
 */
export function computeThemeVars(theme: PanelTheme): Record<string, string> {
    const dark = isDarkTheme(theme);
    // The direction "away from the background" — what raising a surface or
    // strengthening a border moves toward.
    const fg = dark ? "#ffffff" : "#000000";
    const bgward = dark ? "#000000" : "#ffffff";

    return {
        // ---- pre-existing names, unchanged ----
        "--ada-panel-bg": theme.bg,
        "--ada-panel-border": theme.border,
        "--ada-panel-text": theme.text,
        "--ada-panel-surface": theme.surface,
        "--ada-panel-text-muted": theme.textMuted,
        "--ada-accent": SEMANTIC_COLORS.accent,
        "--ada-pass": SEMANTIC_COLORS.pass,
        "--ada-warn": SEMANTIC_COLORS.warn,
        "--ada-fail": SEMANTIC_COLORS.fail,

        // ---- surfaces ----
        // 0 = app chrome (behind everything), 1 = panel, 2 = row/input, 3 = hover/raised.
        "--ada-surface-0": dark ? APP_BG.dark : APP_BG.light,
        "--ada-surface-1": theme.bg,
        "--ada-surface-2": theme.surface,
        "--ada-surface-3": mix(theme.surface, fg, 88),

        // ---- text ----
        "--ada-text": theme.text,
        "--ada-text-muted": theme.textMuted,
        "--ada-text-subtle": mix(theme.textMuted, bgward, 62),

        // ---- borders ----
        "--ada-border": theme.border,
        "--ada-border-strong": mix(theme.border, fg, 65),

        // ---- focus ----
        // Deliberately NOT the plain accent: the ring must stay visible where an accent
        // fill already sits behind it (a focused primary Button).
        "--ada-focus-ring": mix(SEMANTIC_COLORS.accent, fg, 70),

        // ---- accent ----
        "--ada-accent-hover": mix(SEMANTIC_COLORS.accent, fg, 82),
        "--ada-accent-fg": "#ffffff", // text ON an accent fill; accent is dark enough in both directions
        "--ada-accent-subtle": mix(SEMANTIC_COLORS.accent, "transparent", 18),

        // ---- status, each with a subtle fill for badges/callouts ----
        "--ada-info": SEMANTIC_COLORS.info,
        "--ada-pass-subtle": mix(SEMANTIC_COLORS.pass, "transparent", 18),
        "--ada-warn-subtle": mix(SEMANTIC_COLORS.warn, "transparent", 18),
        "--ada-fail-subtle": mix(SEMANTIC_COLORS.fail, "transparent", 18),
        "--ada-info-subtle": mix(SEMANTIC_COLORS.info, "transparent", 18),

        // ---- selection ----
        // Same constant the three.js highlight material uses, so a selected row and the
        // selected geometry are the same colour by construction.
        "--ada-select": SELECTION_COLOR,
        "--ada-select-subtle": mix(SELECTION_COLOR, "transparent", 22),
    };
}

/** Every custom property `computeThemeVars` emits. Used by the token test and by the
 *  embed's scoped-root application to know what to clear. */
export const THEME_VAR_NAMES = Object.keys(computeThemeVars({
    bg: "#000000", border: "#000000", text: "#ffffff", surface: "#000000", textMuted: "#888888",
})) as readonly string[];
