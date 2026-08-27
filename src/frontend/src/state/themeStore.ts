import {create} from "zustand";
import {persist} from "zustand/middleware";
import type {PluginTheme} from "@/plugins/registry";

// Panel theming for the menu-row info boxes (Options / Storage /
// Selected Object / Scene / Server / WS status). The panels read
// their chrome from CSS custom properties (--ada-panel-*) via the
// shared PANEL_CHROME class string; this store owns the values and
// writes them onto <html> whenever they change, so switching theme
// re-paints every panel without prop drilling.
//
// Why themable at all: panel chrome is a trade-off between text
// legibility and how much attention the box steals from the 3D view.
// The dark default reads best; the pale glass distracts least. The
// two middle presets split the difference, and the custom swatches
// let the user land anywhere.

export interface PanelTheme {
    /** Any CSS color — presets use rgba() so the alpha rides along. */
    bg: string;
    border: string;
    text: string;
    /** A raised sub-surface (rows / inputs / nested cards) that reads as one
     *  step off `bg` in the same light/dark direction as the preset. */
    surface: string;
    /** De-emphasised text (labels, captions) against `bg`. */
    textMuted: string;
}

export const THEME_PRESETS: Record<string, {name: string; hint: string; theme: PanelTheme}> = {
    slate: {
        name: "Slate glass",
        hint: "Dark but translucent — the scene shows through",
        theme: {bg: "rgba(30, 41, 59, 0.62)", border: "rgba(148, 163, 184, 0.35)", text: "#f1f5f9", surface: "rgba(148, 163, 184, 0.16)", textMuted: "#94a3b8"},
    },
    dark: {
        name: "Dark",
        hint: "High contrast, easiest to read",
        theme: {bg: "rgba(17, 24, 39, 0.95)", border: "rgba(55, 65, 81, 1)", text: "#f3f4f6", surface: "rgba(255, 255, 255, 0.06)", textMuted: "#9ca3af"},
    },
    mist: {
        name: "Mist",
        hint: "Light glass with dark text",
        theme: {bg: "rgba(226, 232, 240, 0.55)", border: "rgba(71, 85, 105, 0.4)", text: "#111827", surface: "rgba(15, 23, 42, 0.06)", textMuted: "#475569"},
    },
    pale: {
        name: "Pale glass",
        hint: "The classic unobtrusive gray",
        theme: {bg: "rgba(156, 163, 175, 0.5)", border: "rgba(156, 163, 175, 0)", text: "#ffffff", surface: "rgba(255, 255, 255, 0.18)", textMuted: "#e5e7eb"},
    },
};

/** Theme-neutral semantic colours — mid-tones chosen to read on both the light
 *  and dark panel presets. Not user-themable (the presets only re-chrome the
 *  panel surround); a plugin uses these for interactive / status accents so
 *  Scene, Procedural and plugin panels share one status vocabulary. */
export const SEMANTIC_TOKENS = {
    accent: "#3b82f6",
    pass: "#22c55e",
    warn: "#f59e0b",
    fail: "#ef4444",
} as const;

export type ThemePresetId = keyof typeof THEME_PRESETS;

/** Shared chrome class for every menu-row panel. Color comes from the
 *  CSS vars this store maintains; shape/elevation stay constant. */
export const PANEL_CHROME =
    "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
    "text-[var(--ada-panel-text)] shadow-lg rounded-md p-2";

interface ThemeState {
    preset: ThemePresetId;
    /** Hex overrides from the custom swatches; null = use the preset. */
    customBg: string | null;
    customText: string | null;
    /** Alpha applied to customBg (presets carry their own alpha). */
    bgOpacity: number;
    setPreset: (p: ThemePresetId) => void;
    setCustomBg: (hex: string) => void;
    setCustomText: (hex: string) => void;
    setBgOpacity: (a: number) => void;
    resetCustom: () => void;
}

function hexToRgba(hex: string, alpha: number): string {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export function effectivePanelTheme(s: Pick<ThemeState, "preset" | "customBg" | "customText" | "bgOpacity">): PanelTheme {
    const base = (THEME_PRESETS[s.preset] ?? THEME_PRESETS.slate).theme;
    return {
        bg: s.customBg ? hexToRgba(s.customBg, s.bgOpacity) : base.bg,
        border: base.border,
        text: s.customText ?? base.text,
        // Custom swatches only re-tint bg/text; surface + muted stay the
        // preset's so the raised/quiet steps keep their light/dark direction.
        surface: base.surface,
        textMuted: base.textMuted,
    };
}

/** The full token set core hands plugins via `AdaPluginContext.theme`: the
 *  user-themable panel chrome plus the theme-neutral semantic colours. Kept in
 *  lockstep with the `--ada-*` CSS vars written below so a plugin can consume
 *  either shape. */
export function effectivePluginTheme(
    s: Pick<ThemeState, "preset" | "customBg" | "customText" | "bgOpacity">,
): PluginTheme {
    return {...effectivePanelTheme(s), ...SEMANTIC_TOKENS};
}

/** The plugin theme, as a SUBSCRIPTION rather than a snapshot.
 *
 * `effectivePluginTheme(useThemeStore.getState())` reads the store without
 * subscribing, so a component using it repaints only when something else
 * happens to re-render it. Core's own chrome does not notice, because it paints
 * from the `--ada-*` CSS variables, which the store writes to the document
 * element and the browser applies immediately. A plugin panel handed the token
 * OBJECT has no such luck: it keeps whatever values were current at its last
 * render, so switching theme repaints core and leaves plugin panels behind.
 *
 * The four fields below are exactly the ones `effectivePluginTheme` derives
 * from, and all four are primitives — so this subscribes precisely, with no
 * equality function and no re-render on unrelated store writes. */
export function usePluginTheme(): PluginTheme {
    const preset = useThemeStore((s) => s.preset);
    const customBg = useThemeStore((s) => s.customBg);
    const customText = useThemeStore((s) => s.customText);
    const bgOpacity = useThemeStore((s) => s.bgOpacity);
    return effectivePluginTheme({preset, customBg, customText, bgOpacity});
}

function applyPanelThemeVars(theme: PanelTheme): void {
    const root = document.documentElement.style;
    root.setProperty("--ada-panel-bg", theme.bg);
    root.setProperty("--ada-panel-border", theme.border);
    root.setProperty("--ada-panel-text", theme.text);
    // Extended tokens for plugin panels (see effectivePluginTheme). The panel
    // chrome above stays the source of truth for core's own boxes; these add
    // the raised-surface / muted-text / semantic accents plugins need to match.
    root.setProperty("--ada-panel-surface", theme.surface);
    root.setProperty("--ada-panel-text-muted", theme.textMuted);
    root.setProperty("--ada-accent", SEMANTIC_TOKENS.accent);
    root.setProperty("--ada-pass", SEMANTIC_TOKENS.pass);
    root.setProperty("--ada-warn", SEMANTIC_TOKENS.warn);
    root.setProperty("--ada-fail", SEMANTIC_TOKENS.fail);
}

export const useThemeStore = create<ThemeState>()(
    persist(
        (set) => ({
            preset: "slate",
            customBg: null,
            customText: null,
            bgOpacity: 0.9,
            // Picking a preset clears the custom swatches — the preset
            // IS the chosen look; stale overrides shadowing it would
            // make the preset buttons feel broken.
            setPreset: (p) => set({preset: p, customBg: null, customText: null}),
            setCustomBg: (hex) => set({customBg: hex}),
            setCustomText: (hex) => set({customText: hex}),
            setBgOpacity: (a) => set({bgOpacity: Math.min(1, Math.max(0.1, a))}),
            resetCustom: () => set({customBg: null, customText: null}),
        }),
        {name: "ada-panel-theme"},
    ),
);

// Paint on import (default or persisted snapshot) and on every change —
// including the async persist rehydration, which fires subscribers.
applyPanelThemeVars(effectivePanelTheme(useThemeStore.getState()));
useThemeStore.subscribe((s) => applyPanelThemeVars(effectivePanelTheme(s)));
