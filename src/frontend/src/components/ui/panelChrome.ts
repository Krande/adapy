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

// Windows and the split grid's groups read their colours from these custom
// properties, each falling back to the classic look, so nothing sets them by
// default and a shell restyles them through `UiShellSpec.themeTokens`:
//
//   --ada-window-bg            a window's body             (gray-900)
//   --ada-window-border        its border and title rule   (gray-700)
//   --ada-window-header-bg     a title bar, a group's tab strip (gray-800)
//   --ada-window-header-text   title bar text              (gray-100)
//   --ada-window-button-bg     a title bar button          (gray-700 at 60 %)
//   --ada-window-button-border                             (gray-600)
//   --ada-tab-active-bg        a group's shown tab         (gray-700)
//   --ada-tab-active-text                                  (white)
//   --ada-tab-text             the other tabs              (gray-400)
//   --ada-group-divider        the bar between groups      (gray-700)

/** A floating window's card. */
export const WINDOW_CARD =
  "h-full w-full flex flex-col bg-[var(--ada-window-bg,#111827)] border border-[var(--ada-window-border,#374151)] " +
  "sm:rounded-sm shadow-2xl overflow-hidden";

/** A floating window's title bar, after the drag-handle classes when it is draggable. */
export const WINDOW_HEADER =
  "flex items-center gap-2 bg-[var(--ada-window-header-bg,#1f2937)] text-[var(--ada-window-header-text,#f3f4f6)] " +
  "px-3 py-2 border-b border-[var(--ada-window-border,#374151)] shrink-0";

/** A button in a floating window's title bar. */
export const WINDOW_HEADER_BUTTON =
  "shrink-0 inline-flex items-center justify-center w-9 h-9 sm:w-7 sm:h-7 rounded-sm " +
  "border border-[var(--ada-window-button-border,#4b5563)] bg-[var(--ada-window-button-bg,rgb(55_65_81/0.6))] " +
  "text-gray-200 hover:bg-gray-600 hover:border-gray-400 hover:text-white";

/** A split-grid group's tab strip. */
export const GROUP_HEADER =
  "flex items-center gap-1 h-9 shrink-0 pl-1 pr-1 border-b border-[var(--ada-panel-border)] " +
  "bg-[var(--ada-window-header-bg,#1f2937)]";

/** A tab of a group's strip, by whether it is the shown one. */
export function groupTabClass(selected: boolean): string {
  return selected
    ? "bg-[var(--ada-tab-active-bg,#374151)] text-[var(--ada-tab-active-text,#ffffff)]"
    : "text-[var(--ada-tab-text,#9ca3af)] hover:text-white hover:bg-white/10";
}

/** The bar between two groups (or a group and the view). */
export const GROUP_DIVIDER = "bg-[var(--ada-group-divider,#374151)]";

/** The title bar's class: the drag handle (when draggable) first, then the bar. */
export function windowHeaderClass(dragHandleClassName?: string | null): string {
  return (dragHandleClassName ? `${dragHandleClassName} cursor-move ` : "") + WINDOW_HEADER;
}
