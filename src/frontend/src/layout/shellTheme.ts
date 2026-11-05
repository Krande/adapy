// A composed shell's look: its `themeTokens` on the document root and its
// `bodyPalette` under core's panel bodies. Applied by the layout and by core's
// canvas-less pages (`/convert`, `/admin`), which the same shell dresses.

import React, { useEffect } from "react";

import { isComposedShell, type UiShellSpec } from "@/plugins/uiShells";
import { usePluginTheme } from "@/state/themeStore";

import { useBodyPalette } from "./bodyPalette";

/** Set the shell's tokens on the document root while mounted (again on a theme change). Portalled overlays read them too. */
export function useThemeTokens(spec: UiShellSpec["themeTokens"]): void {
  const theme = usePluginTheme();
  const tokens = React.useMemo(() => (typeof spec === "function" ? spec(theme) : spec), [spec, theme]);
  useEffect(() => {
    if (!tokens) return;
    const root = document.documentElement.style;
    const before = Object.keys(tokens).map((k) => [k, root.getPropertyValue(k)] as const);
    for (const [k, v] of Object.entries(tokens)) root.setProperty(k, v);
    return () => {
      for (const [k, v] of before) {
        if (v) root.setProperty(k, v);
        else root.removeProperty(k);
      }
    };
  }, [tokens]);
}

/** The tokens and the body palette of `spec`, while mounted. */
export function useShellLook(spec: Pick<UiShellSpec, "themeTokens" | "bodyPalette" | "panels"> | undefined): void {
  useThemeTokens(spec?.themeTokens);
  useBodyPalette(spec?.bodyPalette, spec?.panels);
}

/** The look of the active shell on a page of core's own root: only a composed shell has one for core to apply. */
export function useComposedShellLook(spec: (Pick<UiShellSpec, "themeTokens" | "bodyPalette" | "panels" | "load">) | undefined): void {
  useShellLook(spec && isComposedShell(spec) ? spec : undefined);
}
