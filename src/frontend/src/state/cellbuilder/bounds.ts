/**
 * Topology EXTENTS in model space.
 *
 * Owns: the width and the far +X edge of the cell model — what the
 * side-by-side view offsets the compiled result by. Pure functions over a
 * cells map.
 */

import type {BuilderCell} from "./types";

/** The topology's X-width from its cells (0 when empty). */
export function modelXWidth(cells: Record<string, BuilderCell>): number {
  let minX = Infinity;
  let maxX = -Infinity;
  for (const c of Object.values(cells)) {
    minX = Math.min(minX, c.origin[0]);
    maxX = Math.max(maxX, c.origin[0] + c.size[0]);
  }
  return maxX > minX ? maxX - minX : 0;
}

/** The topology's far +X EDGE in model space (0 when empty).
 *
 * This, not the width, is what the side-by-side offset needs: the result has to
 * start past where the topology ENDS. A cell model authored from the origin
 * outward has edge == width, which is why a width-based formula appeared to
 * work — but the two diverge the moment cells do not start at 0, and the result
 * then overlaps by exactly that difference. */
export function modelMaxX(cells: Record<string, BuilderCell>): number {
  let maxX = -Infinity;
  for (const c of Object.values(cells)) {
    maxX = Math.max(maxX, c.origin[0] + c.size[0]);
  }
  return Number.isFinite(maxX) && maxX > 0 ? maxX : 0;
}
