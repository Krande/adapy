// Pure helpers for the catalogue equipment 3D preview's bounding-box wireframe.
//
// The preview is a Z-up scene, IDENTICAL to the main cellbuilder view: the box
// is BoxGeometry(lx, ly, lz) with the footprint on X/Y and LZ vertical (along
// +Z = up) = the height, base sitting at z=0. The dims come from the backend
// `infer bbox` job, which returns Z-up lx/ly/lz (lz = height) and a Z-up preview
// GLB — so the box, the CAD mesh, and these numbers are all in the same frame,
// no client-side axis juggling. Keeping the math here (arrays, no THREE types)
// makes it unit-testable.

export interface EquipmentBbox {
  lx: number;
  ly: number;
  lz: number;
}

/** Centre + size of the Z-up equipment box, for a BoxGeometry + its position.
 * Base at z=0, centred on X/Y, so the centre is (0, 0, lz/2). Sizes clamp ≥ 0. */
export function equipmentBoxCenterSize(b: EquipmentBbox): {
  center: [number, number, number];
  size: [number, number, number];
} {
  const lx = Math.max(0, b.lx);
  const ly = Math.max(0, b.ly);
  const lz = Math.max(0, b.lz);
  return { center: [0, 0, lz / 2], size: [lx, ly, lz] };
}

/** The eight Z-up corners of the equipment box (footprint on X/Y ∈ ±l/2, height
 * on Z ∈ [0, lz]). Used as port snap targets. */
export function equipmentBoxCorners(b: EquipmentBbox): [number, number, number][] {
  const out: [number, number, number][] = [];
  for (const x of [-b.lx / 2, b.lx / 2])
    for (const y of [-b.ly / 2, b.ly / 2]) for (const z of [0, b.lz]) out.push([x, y, z]);
  return out;
}

/** Whether a bbox looks un-inferred / pre-normalization: a non-positive extent,
 * or the cubic default (all three equal, e.g. the 1×1×1 seed). With a CAD
 * attached that's the trigger to auto-(re)infer so the backend regenerates Z-up
 * dims + a Z-up preview GLB (self-heal for types created before the fix). */
export function bboxLooksUninferred(b: EquipmentBbox): boolean {
  const { lx, ly, lz } = b;
  if (!(lx > 0) || !(ly > 0) || !(lz > 0)) return true;
  const eq = (a: number, c: number) => Math.abs(a - c) < 1e-6;
  return eq(lx, ly) && eq(ly, lz);
}
