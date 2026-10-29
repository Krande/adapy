/** Whether a click in the 3D view adds to the selection (toggling what it hits) rather than
 *  replacing it: Shift, as before, and Ctrl (Cmd on macOS) -- the modifier the scene tree and most
 *  CAD tools use for the same thing, so a selection started in the tree carries on in the view. */
export function isAdditiveClick(e: Pick<MouseEvent, "shiftKey" | "ctrlKey" | "metaKey">): boolean {
  return e.shiftKey || e.ctrlKey || e.metaKey;
}
