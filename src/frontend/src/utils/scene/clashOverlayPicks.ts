// A click on the clash overlay (a joint marker, a contact patch), on whichever viewport draws it.
//
// The overlay is drawn through the viewport's layers (`clashJointMarkers`), which take no part in
// picking -- so what it drew is kept here, in the world, and a click is matched against it on the
// screen (`ViewportApi.projectToScreen`): a marker within a few pixels of the cursor, else the
// nearest patch triangle under it. Each viewport's click handling asks here before it picks an object
// of the model: a marker or a patch is drawn ON the geometry it describes, and asked after the model
// it would never win.
//
// Dependency-free (no three.js, no stores): the three.js-free viewport's click handler imports it.

import type { ViewportApi } from "@/viewport/api/viewportApi";

/** What the overlay draws, in world coordinates. */
export interface ClashPicks {
  /** Three numbers per marker. */
  readonly markers: Float32Array;
  /** Per marker: the joint it is, and its radius on screen in CSS pixels. */
  readonly markerIds: readonly string[];
  readonly markerRadiiPx: readonly number[];
  /** Nine numbers per triangle of the contact patches. */
  readonly triangles: Float32Array;
  /** Per triangle: the contact it was drawn for. */
  readonly triangleIds: readonly string[];
}

/** How far from a marker's centre a click still takes it, at the least. */
const REACH_PX = 6;

let shown: { viewport: ViewportApi; picks: ClashPicks; onPick: (id: string) => void } | null = null;

/** What `viewport` draws now (null: nothing), and what a click on it does. */
export function setShownClashPicks(viewport: ViewportApi | null, picks: ClashPicks | null, onPick?: (id: string) => void): void {
  shown = viewport && picks && onPick ? { viewport, picks, onPick } : null;
}

/** The joint or contact drawn at a point of `viewport`'s view (CSS pixels from its corner), or null. */
export function clashPickAt(viewport: ViewportApi, x: number, y: number): string | null {
  if (!shown || shown.viewport !== viewport) return null;
  const { markers, markerIds, markerRadiiPx, triangles, triangleIds } = shown.picks;
  const project = (world: Float32Array, count: number) => {
    const out = new Float32Array(count * 3);
    viewport.projectToScreen(world, count, out);
    return out;
  };

  // A marker first: the nearest to the cursor within its reach, the nearer one to the eye on a tie.
  if (markerIds.length) {
    const screen = project(markers, markerIds.length);
    let best: { id: string; d: number; depth: number } | null = null;
    for (let i = 0; i < markerIds.length; i++) {
      const sx = screen[i * 3], sy = screen[i * 3 + 1], depth = screen[i * 3 + 2];
      if (!(sx === sx) || depth < 0 || depth > 1) continue;
      const d = Math.hypot(x - sx, y - sy);
      if (d > Math.max(REACH_PX, markerRadiiPx[i] ?? 0)) continue;
      if (!best || d < best.d - 1e-6 || (Math.abs(d - best.d) <= 1e-6 && depth < best.depth)) best = { id: markerIds[i], d, depth };
    }
    if (best) return best.id;
  }

  // Then a patch: the triangle under the cursor nearest to the eye.
  if (triangleIds.length) {
    const screen = project(triangles, triangleIds.length * 3);
    let best: { id: string; depth: number } | null = null;
    for (let t = 0; t < triangleIds.length; t++) {
      const o = t * 9;
      const ax = screen[o], ay = screen[o + 1], bx = screen[o + 3], by = screen[o + 4], cx = screen[o + 6], cy = screen[o + 7];
      if (!(ax === ax && bx === bx && cx === cx)) continue;
      if (!inTriangle(x, y, ax, ay, bx, by, cx, cy)) continue;
      const depth = (screen[o + 2] + screen[o + 5] + screen[o + 8]) / 3;
      if (depth < 0 || depth > 1) continue;
      if (!best || depth < best.depth) best = { id: triangleIds[t], depth };
    }
    if (best) return best.id;
  }
  return null;
}

/** A click at a point of `viewport`'s view: focuses the joint drawn there and says true, or says false
 *  for the viewport to go on and pick an object. */
export function takeClashClick(viewport: ViewportApi, x: number, y: number): boolean {
  const id = clashPickAt(viewport, x, y);
  if (!id || !shown) return false;
  shown.onPick(id);
  return true;
}

function inTriangle(px: number, py: number, ax: number, ay: number, bx: number, by: number, cx: number, cy: number): boolean {
  const d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by);
  const d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy);
  const d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay);
  const neg = d1 < 0 || d2 < 0 || d3 < 0;
  const pos = d1 > 0 || d2 > 0 || d3 > 0;
  return !(neg && pos);
}
