/**
 * Pure geometry for the read-only loft (swept-band) cell viewer — no three.js,
 * node-testable. Mirrors the Phase 2a backend contract
 * (ada.topology.entities.LoftStation / TopoLoftMember): a member with N
 * stations decomposes into N-1 inter-station bands; band i is swept between
 * STATIONS[i] -> STATIONS[i+1], named `${NAME}_bay${i}` (matching the compiled
 * cell metadata member=NAME, station_lo=i, station_hi=i+1 so a picked band
 * links to the same cell). A station ring is redrawn directly from its params:
 * rectangle = 4 corners centred at (X,Y) from WIDTH/HEIGHT in the z==Z plane;
 * circle = SEGMENTS-sampled polygon of RADIUS; then PLACEMENT (a 4x4 row-major
 * affine, identity when absent) is applied to each point.
 */

import type { Vec3 } from "./snap";

/** One authored station: a section profile at a spine position. Extra keys are
 * carried through (the info panel only reads the curated subset). */
export interface LoftStation {
  TYPE: "rectangle" | "circle";
  X: number;
  Y: number;
  Z: number;
  WIDTH?: number;
  HEIGHT?: number;
  RADIUS?: number;
  SEGMENTS?: number;
  [k: string]: unknown;
}

/** One authored loft member as it appears in the procedural doc's
 * ``loft_members`` array. Re-emitted verbatim on commit (read-only slice). */
export interface LoftMemberDoc {
  NAME: string;
  STRUCTURE_NAME?: string;
  INCLUDE?: boolean;
  STATIONS: LoftStation[];
  /** Optional 4x4 row-major affine (nested list or flat 16); identity when
   * absent. Applied to every station ring point. */
  PLACEMENT?: number[][] | number[] | null;
  THICKNESS?: number;
  SURFACE_ONLY?: boolean;
  [k: string]: unknown;
}

/** A closed ring of world-frame points (rectangle = 4, circle = SEGMENTS). */
export type Ring = Vec3[];

/** Derived metadata carried on a loft-band BuilderCell — the two placed rings
 * plus the identity/param info the renderer + info panel need. */
export interface LoftBand {
  member: string;
  /** Band index i (0-based); a member with N stations has N-1 bays. */
  bay: number;
  /** Total bays in the parent member (i of `bandCount`). */
  bandCount: number;
  /** Cell name matching the compiled metadata: `${member}_bay${i}`. */
  cellName: string;
  /** [lo ring, hi ring] — swept bottom -> top, world (placed) points. */
  rings: [Ring, Ring];
  stationLo: LoftStation;
  stationHi: LoftStation;
}

const DEFAULT_SEGMENTS = 16;

/** Normalize a PLACEMENT (nested 4x4 or flat 16, row-major) to 4 rows of 4.
 * Returns null for an absent/malformed matrix (treated as identity). */
function normPlacement(
  p: number[][] | number[] | null | undefined,
): number[][] | null {
  if (p == null) return null;
  // Flat 16 -> 4x4.
  if (typeof p[0] === "number") {
    const flat = p as number[];
    if (flat.length !== 16) return null;
    return [
      [flat[0], flat[1], flat[2], flat[3]],
      [flat[4], flat[5], flat[6], flat[7]],
      [flat[8], flat[9], flat[10], flat[11]],
      [flat[12], flat[13], flat[14], flat[15]],
    ];
  }
  const rows = p as number[][];
  if (rows.length !== 4 || rows.some((r) => r.length !== 4)) return null;
  return rows;
}

/** Apply a row-major 4x4 affine to a point (identity when mat is null). */
export function applyPlacement(mat: number[][] | null, pt: Vec3): Vec3 {
  if (!mat) return pt;
  const [x, y, z] = pt;
  return [
    mat[0][0] * x + mat[0][1] * y + mat[0][2] * z + mat[0][3],
    mat[1][0] * x + mat[1][1] * y + mat[1][2] * z + mat[1][3],
    mat[2][0] * x + mat[2][1] * y + mat[2][2] * z + mat[2][3],
  ];
}

/** The closed ring of world points for a station, PLACEMENT applied. Rectangle
 * corners order (-,-),(+,-),(+,+),(-,+) and circle CCW sampling both match the
 * backend LoftStation.to_poly_loop so a redrawn ring registers with its plate. */
export function stationRingPoints(
  station: LoftStation,
  placement?: number[][] | number[] | null,
): Ring {
  const mat = normPlacement(placement);
  const { X, Y, Z } = station;
  const local: Vec3[] = [];
  if (station.TYPE === "circle") {
    const r = station.RADIUS ?? 0;
    const n = Math.max(3, Math.floor(station.SEGMENTS ?? DEFAULT_SEGMENTS));
    for (let i = 0; i < n; i++) {
      const a = (2 * Math.PI * i) / n;
      local.push([X + r * Math.cos(a), Y + r * Math.sin(a), Z]);
    }
  } else {
    // rectangle (default)
    const hw = (station.WIDTH ?? 0) / 2;
    const hh = (station.HEIGHT ?? 0) / 2;
    local.push(
      [X - hw, Y - hh, Z],
      [X + hw, Y - hh, Z],
      [X + hw, Y + hh, Z],
      [X - hw, Y + hh, Z],
    );
  }
  return local.map((pt) => applyPlacement(mat, pt));
}

/** Split a member into its N-1 swept bands (empty when < 2 stations, or when
 * INCLUDE is explicitly false). Each band carries the two placed rings + the
 * station params, keyed `${NAME}_bay${i}`. */
export function memberToBands(member: LoftMemberDoc): LoftBand[] {
  if (member.INCLUDE === false) return [];
  const stations = member.STATIONS ?? [];
  if (stations.length < 2) return [];
  const rings = stations.map((st) => stationRingPoints(st, member.PLACEMENT));
  const bandCount = stations.length - 1;
  const out: LoftBand[] = [];
  for (let i = 0; i < bandCount; i++) {
    out.push({
      member: member.NAME,
      bay: i,
      bandCount,
      cellName: `${member.NAME}_bay${i}`,
      rings: [rings[i], rings[i + 1]],
      stationLo: stations[i],
      stationHi: stations[i + 1],
    });
  }
  return out;
}

/** Axis-aligned bounding box (min corner + size) of a band's two rings. Used
 * for the BuilderCell origin/size fields (loft cells are drawn from their
 * rings, not this box, but the box keeps box-oriented plumbing safe). */
export function bandBounds(band: LoftBand): { origin: Vec3; size: Vec3 } {
  const pts = [...band.rings[0], ...band.rings[1]];
  const min: Vec3 = [Infinity, Infinity, Infinity];
  const max: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const p of pts) {
    for (let a = 0; a < 3; a++) {
      if (p[a] < min[a]) min[a] = p[a];
      if (p[a] > max[a]) max[a] = p[a];
    }
  }
  if (!pts.length) return { origin: [0, 0, 0], size: [0, 0, 0] };
  return {
    origin: min,
    size: [max[0] - min[0], max[1] - min[1], max[2] - min[2]],
  };
}
