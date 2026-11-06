// Pop-out windows: browser windows of their own that hold panels, so several
// windows (on several monitors) form one dashboard with the main window.
//
//   * `?window=<id>` boots the viewer canvas-less as the pop-out window `<id>`
//     (`AppRoot` in `@/app` routes it): a split grid of panel groups of its own,
//     arranged by the user and kept under that id (`./PopoutPage`).
//   * `?popout=<panelId>` opens a new pop-out window holding that panel; with
//     `?window=` too, that window gets the panel added on first load.
//
// Only panels that opt in may go there (`canPopOut`): a pop-out window has stores
// of its own and no 3D scene, so a panel that acts on the scene must be mirrored
// from the main window to work there (`capabilities.mirrored`, `./windowMirror`).
//
// Dependency-free, so the facade root can export it.

import type { PanelId, RegisteredPanel } from "./panel";
import { isSessionId, isWindowId, type WindowId } from "./windowBus";

/** The URL parameter naming a panel a new pop-out window shows. */
export const POPOUT_PARAM = "popout";
/** The URL parameter naming the pop-out window a page is. */
export const WINDOW_PARAM = "window";

/** The URL parameter naming the dashboard session a pop-out window belongs to (its main window's). */
export const SESSION_PARAM = "session";

/** The dashboard session in the URL (`?session=`); null when absent or malformed. */
export function popoutSession(search: string = typeof window !== "undefined" ? window.location.search : ""): string | null {
  const id = new URLSearchParams(search).get(SESSION_PARAM);
  return isSessionId(id) ? id : null;
}

/** The panel id a new pop-out window should show; null for a normal tab. */
export function popoutPanelId(search: string = typeof window !== "undefined" ? window.location.search : ""): PanelId | null {
  return new URLSearchParams(search).get(POPOUT_PARAM) || null;
}

/** The pop-out window id this page is (`?window=`); null for the main window or a malformed id. */
export function popoutWindowId(search: string = typeof window !== "undefined" ? window.location.search : ""): WindowId | null {
  const id = new URLSearchParams(search).get(WINDOW_PARAM);
  return isWindowId(id) ? id : null;
}

/** Is this page a pop-out window (either parameter). */
export function isPopoutPage(search: string = typeof window !== "undefined" ? window.location.search : ""): boolean {
  return popoutWindowId(search) !== null || popoutPanelId(search) !== null;
}

/**
 * May go to a pop-out window: it opts in (`popout`), has something to draw, and
 * needs no canvas -- or is mirrored from the main window (`mirrored`), which then
 * stands in for the scene.
 */
export function canPopOut(panel: Pick<RegisteredPanel, "capabilities" | "constraints" | "content" | "body">): boolean {
  if (!(panel.content ?? panel.body)) return false;
  if (panel.capabilities.mirrored) return true;
  return panel.capabilities.popout && !panel.constraints?.requiresCanvas;
}

/** A pop-out URL on this page's origin and path: what selects the UI kept, what loads models dropped. */
function baseUrl(href: string): URL {
  const url = new URL(href);
  for (const k of [...url.searchParams.keys()]) url.searchParams.delete(k);
  const from = new URL(href).searchParams;
  for (const keep of ["ui", "plugins", "scope"]) {
    const v = from.get(keep);
    if (v) url.searchParams.set(keep, v);
  }
  url.hash = "";
  return url;
}

/**
 * The URL of a new pop-out window holding `id` (the window's id chosen when it
 * loads). `session` ties it to a dashboard session; without one it is standalone.
 */
export function popoutUrl(id: PanelId, href: string = window.location.href, session?: string | null): string {
  const url = baseUrl(href);
  url.searchParams.set(POPOUT_PARAM, id);
  if (session) url.searchParams.set(SESSION_PARAM, session);
  return url.toString();
}

/** The URL of pop-out window `windowId` of dashboard session `session`, adding `panel` to it when given. */
export function windowUrl(windowId: WindowId, panel?: PanelId, href: string = window.location.href, session?: string | null): string {
  const url = baseUrl(href);
  url.searchParams.set(WINDOW_PARAM, windowId);
  if (session) url.searchParams.set(SESSION_PARAM, session);
  if (panel) url.searchParams.set(POPOUT_PARAM, panel);
  return url.toString();
}

/** The browser window name of pop-out `windowId` of `session`: opening it again focuses it; another session's same id does not collide. */
export const windowTarget = (windowId: WindowId, session?: string | null): string =>
  session ? `ada-window-${session}-${windowId}` : `ada-window-${windowId}`;

const FEATURES = "popup,width=720,height=760";

/** Open (or focus) pop-out window `windowId` of `session`, adding `panel` to it. Its handle; null when the browser blocked it. */
export function openWindow(windowId: WindowId, panel?: PanelId, session?: string | null): Window | null {
  const win = window.open(windowUrl(windowId, panel, undefined, session), windowTarget(windowId, session), FEATURES);
  if (!win) return null;
  win.focus?.();
  return win;
}

/**
 * Open `id` in a new window of its own and hide it here, so it is not shown
 * twice. `windowId` names the new window (default: one derived from the panel).
 * False when the browser blocked the window.
 */
export function openPopout(id: PanelId, hide: (id: PanelId) => unknown, windowId?: WindowId, session?: string | null): boolean {
  const target = windowId ?? `p-${id.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 36)}`;
  if (!openWindow(target, id, session)) return false;
  hide(id);
  return true;
}
