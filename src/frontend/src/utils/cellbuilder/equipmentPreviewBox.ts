// Pure helpers for the catalogue equipment 3D preview's bounding-box wireframe.
//
// The preview must WRAP the CAD regardless of what (possibly stale / default
// 1×1×1) lx/ly/lz the type has stored and regardless of the preview GLB's
// authored orientation/units — so when a CAD mesh is loaded we draw the box from
// the CAD group's own measured AABB, overriding the stored dims entirely. Only
// when NO CAD is attached do we fall back to the stored lx/ly/lz box (Z-up
// equipment convention: base at z=0, centred in x/y, lz = height). Keeping the
// math here (arrays, no THREE types) makes it unit-testable; the component just
// feeds it the CAD Box3 min/max it measured with THREE.Box3().setFromObject.

export interface AabbLike {
  min: [number, number, number];
  max: [number, number, number];
}

export interface EquipmentBbox {
  lx: number;
  ly: number;
  lz: number;
}

/** The wireframe box to draw for the preview, as an axis-aligned min/max.
 *
 * - `cadAabb` present  → the CAD's real (non-cubic) measured bounds, verbatim.
 *   The box takes the CAD's actual extents and wraps it, no re-inference needed.
 * - `cadAabb` null     → the stored lx/ly/lz nominal box (base at 0, centred).
 */
export function equipmentDisplayBox(
  cadAabb: AabbLike | null,
  bbox: EquipmentBbox,
): AabbLike {
  if (cadAabb) {
    return { min: [...cadAabb.min], max: [...cadAabb.max] };
  }
  const { lx, ly, lz } = bbox;
  return { min: [-lx / 2, 0, -ly / 2], max: [lx / 2, lz, ly / 2] };
}

/** The eight corners of an AABB (order is irrelevant — used as snap targets). */
export function aabbCorners(box: AabbLike): [number, number, number][] {
  const [x0, y0, z0] = box.min;
  const [x1, y1, z1] = box.max;
  const out: [number, number, number][] = [];
  for (const x of [x0, x1])
    for (const y of [y0, y1]) for (const z of [z0, z1]) out.push([x, y, z]);
  return out;
}

/** Centre + size of an AABB (size clamped to ≥ 0), for a BoxGeometry + position. */
export function aabbCenterSize(box: AabbLike): {
  center: [number, number, number];
  size: [number, number, number];
} {
  const [x0, y0, z0] = box.min;
  const [x1, y1, z1] = box.max;
  return {
    center: [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2],
    size: [Math.max(0, x1 - x0), Math.max(0, y1 - y0), Math.max(0, z1 - z0)],
  };
}
