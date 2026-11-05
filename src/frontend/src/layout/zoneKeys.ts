// Keyboard movement between a preset's zones, with "Customisable panels" on:
//
//   F6 / Shift+F6     focus the next / previous zone (top bar, panel column,
//                     each floating window, the viewport)
//   Esc               close the floating window that has focus
//   Ctrl+Alt+arrow    dock the floating window that has focus
//
// A zone is any element carrying `ZONE_ATTR`; a preset marks its own. This
// module decides; `ZoneKeys` (`./zoneFocus`) listens and moves focus.

/** The attribute that marks a zone, and its kind. */
export const ZONE_ATTR = "data-layout-zone";
/** On a floating window's zone: the panel it holds. */
export const ZONE_PANEL_ATTR = "data-layout-panel";
/** On the action group in a docked panel's own header: the panel's id. */
export const PANEL_ACTIONS_ATTR = "data-panel-actions";
/** On a top-bar toggle: the panel it opens. */
export const PANEL_TOGGLE_ATTR = "data-panel-toggle";

export type ZoneKind = "toolbar" | "column" | "float" | "viewport";

/** F6 order of the kinds; windows keep their document order among themselves. */
export const ZONE_ORDER: readonly ZoneKind[] = ["toolbar", "column", "float", "viewport"];

export type ZoneKeyIntent = "next-zone" | "prev-zone" | "close-float" | "dock-float";

export interface ZoneKeyEvent {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  metaKey: boolean;
  defaultPrevented?: boolean;
}

export interface ZoneKeyContext {
  /** Focus is inside a floating window. */
  inFloat: boolean;
  /** Focus is in a text field, which keeps Esc and the arrows. */
  inTextField: boolean;
  /** A modal dialog or a menu is open, which owns Esc. */
  overlayOpen: boolean;
}

const ARROWS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]);

export function zoneKeyIntent(e: ZoneKeyEvent, ctx: ZoneKeyContext): ZoneKeyIntent | null {
  if (e.key === "F6" && !e.ctrlKey && !e.altKey && !e.metaKey) return e.shiftKey ? "prev-zone" : "next-zone";
  if (e.defaultPrevented || !ctx.inFloat || ctx.inTextField) return null;
  if (e.key === "Escape" && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
    return ctx.overlayOpen ? null : "close-float";
  }
  if (ARROWS.has(e.key) && e.ctrlKey && e.altKey && !e.shiftKey && !e.metaKey) return "dock-float";
  return null;
}

/** The zone F6 moves to from `current` (-1: focus is in none) among `count` zones. */
export function cycleZone(current: number, count: number, dir: 1 | -1): number {
  if (count <= 0) return -1;
  if (current < 0) return dir === 1 ? 0 : count - 1;
  return (current + dir + count) % count;
}

/** Zones sorted into F6 order: by kind, then by document order (the input's). */
export function orderZones<T>(zones: readonly T[], kindOf: (z: T) => string | null): T[] {
  const rank = (z: T) => {
    const i = ZONE_ORDER.indexOf(kindOf(z) as ZoneKind);
    return i < 0 ? ZONE_ORDER.length : i;
  };
  return zones
    .map((z, i) => ({z, i}))
    .sort((a, b) => rank(a.z) - rank(b.z) || a.i - b.i)
    .map((x) => x.z);
}
