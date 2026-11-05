// Opening a panel in a browser window of its own: `?popout=<panelId>` boots the
// viewer canvas-less and shows only that panel (`AppRoot` in `@/app` routes it).
// For panels that work without the 3D scene, and only those that opt in
// (`capabilities.popout`): the new window has stores of its own, so a panel that
// acts on the scene would act on nothing there.
//
// Dependency-free, so the facade root can export it.

import type { PanelId, RegisteredPanel } from "./panel";

/** The URL parameter naming the panel a pop-out window shows. */
export const POPOUT_PARAM = "popout";

/** The panel id a pop-out window shows; null for a normal tab. */
export function popoutPanelId(search: string = typeof window !== "undefined" ? window.location.search : ""): PanelId | null {
  return new URLSearchParams(search).get(POPOUT_PARAM) || null;
}

/** May open in a window of its own: it opts in, needs no canvas, and has something to draw. */
export function canPopOut(panel: Pick<RegisteredPanel, "capabilities" | "constraints" | "content" | "body">): boolean {
  return panel.capabilities.popout && !panel.constraints?.requiresCanvas && !!(panel.content ?? panel.body);
}

/** The URL of `id`'s pop-out window, on this page's origin and path (the hash dropped). */
export function popoutUrl(id: PanelId, href: string = window.location.href): string {
  const url = new URL(href);
  for (const k of [...url.searchParams.keys()]) url.searchParams.delete(k);
  // Keep what selects the build's UI and plugins; drop what loads models or opens other UI.
  const from = new URL(href).searchParams;
  for (const keep of ["ui", "plugins", "scope"]) {
    const v = from.get(keep);
    if (v) url.searchParams.set(keep, v);
  }
  url.searchParams.set(POPOUT_PARAM, id);
  url.hash = "";
  return url.toString();
}

/**
 * Open `id` in a window of its own (one per panel: a second call focuses it) and
 * hide it here, so it is not shown twice. False when the browser blocked the window.
 */
export function openPopout(id: PanelId, hide: (id: PanelId) => unknown): boolean {
  const win = window.open(popoutUrl(id), `ada-popout-${id}`, "popup,width=520,height=720");
  if (!win) return false;
  win.focus?.();
  hide(id);
  return true;
}
