// The class strings of core's panel chrome, defined once.
//
// The menu-column panels share one look: the themable panel colours (CSS custom
// properties the theme store maintains), a border and a shadow. Most add the
// card shape (rounded, padded) on top; a panel with pinned regions -- a tab
// strip that spans edge to edge, a body that scrolls on its own -- takes the
// surface only and pads its regions itself. The surface string used to be
// redefined locally in three panels next to the shared card string.
//
// Floating windows (the in-viewer admin / convert window, the attribute search)
// share a second, darker look: a card with a title bar.
//
// Dependency-free strings, so Tailwind sees every class literally and
// `node --test` can import them.

/** Colours, border and shadow: the surface every menu-column panel is drawn on. */
export const PANEL_SURFACE =
  "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
  "text-[var(--ada-panel-text)] shadow-lg";

/** The menu-column card: the surface, rounded and padded. */
export const PANEL_CHROME = PANEL_SURFACE + " rounded-md p-2";

export type PanelCardVariant = "card" | "surface";

/** A panel root's class: the card or the bare surface, then the panel's own classes. */
export function panelClass(variant: PanelCardVariant, extra?: string): string {
  const base = variant === "card" ? PANEL_CHROME : PANEL_SURFACE;
  return extra ? `${base} ${extra}` : base;
}

/** A floating window's card. */
export const WINDOW_CARD =
  "h-full w-full flex flex-col bg-gray-900 border border-gray-700 sm:rounded-sm shadow-2xl overflow-hidden";

/** A floating window's title bar, after the drag-handle classes when it is draggable. */
export const WINDOW_HEADER =
  "flex items-center gap-2 bg-gray-800 text-gray-100 px-3 py-2 border-b border-gray-700 shrink-0";

/** A button in a floating window's title bar. */
export const WINDOW_HEADER_BUTTON =
  "shrink-0 inline-flex items-center justify-center w-9 h-9 sm:w-7 sm:h-7 rounded-sm border border-gray-600 " +
  "bg-gray-700/60 text-gray-200 hover:bg-gray-600 hover:border-gray-400 hover:text-white";

/** The title bar's class: the drag handle (when draggable) first, then the bar. */
export function windowHeaderClass(dragHandleClassName?: string | null): string {
  return (dragHandleClassName ? `${dragHandleClassName} cursor-move ` : "") + WINDOW_HEADER;
}
