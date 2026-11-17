// How wide the selection tree's drawer is at least (ResizableTreeView.tsx): its own least, or its
// header's content when that is wider -- so the header's buttons are inside the drawer, not past it.

/** The least width whatever the header holds. */
export const MIN_WIDTH = 200;
export const MAX_WIDTH = 560;

/** The header's content: its parts side by side, and its own padding. */
export function headerContentWidth(parts: readonly number[], padding: number): number {
    return parts.reduce((sum, width) => sum + width, 0) + padding;
}

/** The drawer's least width for a header whose content is `headerWidth` wide: never under the
 *  drawer's own least, never over the most it may be dragged to. */
export function drawerMinWidth(headerWidth: number): number {
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.ceil(headerWidth)));
}
