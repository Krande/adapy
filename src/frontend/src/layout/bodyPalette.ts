// A shell's palette for core's panel BODIES (`UiShellSpec.bodyPalette`).
//
// Core's bodies are written in Tailwind's palette (`bg-gray-800`, `text-blue-300`,
// `border-gray-700/60`), and Tailwind 4 compiles each to a custom property
// (`var(--color-gray-800)`). Redefining those properties under every element a body
// is drawn in (`PANEL_BODY_ATTR`) restyles every core body at once, opacity
// modifiers included, without a class changed in core, and leaves the shell's own
// chrome (outside the bodies) as it is.

import { useEffect, useMemo } from "react";

import { bodyPaletteCss } from "@/components/ui/panelChrome";
import type { UiShellSpec } from "@/plugins/uiShells";
import { usePluginTheme } from "@/state/themeStore";

const STYLE_ID = "ada-body-palette";

/** Apply the shell's body palette while mounted (again on a theme change). */
export function useBodyPalette(spec: UiShellSpec["bodyPalette"], panels?: UiShellSpec["panels"]): void {
  const theme = usePluginTheme();
  const css = useMemo(() => {
    const perPanel = Object.fromEntries(Object.entries(panels ?? {}).map(([id, o]) => [id, o.bodyPalette]));
    return bodyPaletteCss(typeof spec === "function" ? spec(theme) : spec, perPanel);
  }, [spec, panels, theme]);
  useEffect(() => {
    if (!css) return;
    let el = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
    if (!el) {
      el = document.createElement("style");
      el.id = STYLE_ID;
      document.head.appendChild(el);
    }
    el.textContent = css;
    return () => el?.remove();
  }, [css]);
}
