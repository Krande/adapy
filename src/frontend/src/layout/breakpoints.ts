// The viewer's responsive breakpoints, named once.
//
// Two widths matter to the panels, and both were spelled out by hand in each
// component that cared (`(max-width: 639px)` in the bottom sheets,
// `(max-width: 767px)` / `(min-width: 768px)` in the windows, the options drawer
// and the top bar):
//
//   * COMPACT (< 640 px, Tailwind's `sm`): a phone. Menu-column panels become
//     bottom sheets.
//   * WIDE (>= 768 px, Tailwind's `md`): a desktop or a landscape tablet. Below
//     it, a floating window takes the whole screen, the options drawer slides in
//     from the left, and the selection tree overlays the top bar instead of
//     pushing it.
//
// The numbers are Tailwind's defaults, so a `sm:` / `md:` class and a query
// from here agree on where the switch happens. Dependency-free.

export const BREAKPOINTS = Object.freeze({
  /** Phones are narrower than this. Tailwind `sm`. */
  compact: 640,
  /** Desktop layouts start here. Tailwind `md`. */
  wide: 768,
});

/** Below `compact`: a phone. */
export const COMPACT_QUERY = `(max-width: ${BREAKPOINTS.compact - 1}px)`;
/** Below `wide`: a phone or a portrait tablet. */
export const NARROW_QUERY = `(max-width: ${BREAKPOINTS.wide - 1}px)`;
/** At or above `wide`. The complement of `NARROW_QUERY`. */
export const WIDE_QUERY = `(min-width: ${BREAKPOINTS.wide}px)`;

/** Does `query` match now. False where there is no window (SSR, `node --test`). */
export function mediaMatches(query: string): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(query).matches
    : false;
}
