import type {DockedId} from "./regions";

// Whether a dock shows its panels stacked or tabbed.
//
// Pure, and separate from DockHost, for the usual reason: the component imports the panel
// registry, which lazy-imports panels that reach stores that reach a vite `?worker&inline`
// module only a bundler can resolve. It is also the half worth asserting — the thresholds
// and the hysteresis are the part that misbehaves, and a browser cannot easily be driven
// to the exact heights that matter.

/**
 * Height a stacked panel needs before stacking is worth it, per panel.
 *
 * ~260px is a header plus roughly six property rows. Below that, a "visible" panel shows
 * a title and a scrollbar, which is worse than a tab: it looks like content is missing
 * rather than merely elsewhere.
 */
export const ENTER_STACK_H = 260;

/**
 * Leave stacking at a lower height than entering it.
 *
 * Without the gap, dragging a splitter across the threshold flips the arrangement back
 * and forth every frame, and a layout that flickers reads as a fault rather than as a
 * feature. The dock's height is set by the grid, so stacking cannot itself change the
 * measurement — this guards the drag, not a feedback loop.
 */
export const LEAVE_STACK_H = 210;

export function shouldStack(args: {
    dock: DockedId;
    panelCount: number;
    heightPx: number;
    wasStacked: boolean;
}): boolean {
    const {dock, panelCount, heightPx, wasStacked} = args;

    // The bottom dock is wide-and-short by design — the FEA table, the conversion log.
    // Stacking wide-and-short panels leaves every one of them too short to read.
    if (dock === "bottom") return false;

    // One panel is not an arrangement. Two tabs where one is the only thing there would
    // be a header for nothing.
    if (panelCount < 2) return false;

    const threshold = (wasStacked ? LEAVE_STACK_H : ENTER_STACK_H) * panelCount;
    return heightPx >= threshold;
}
