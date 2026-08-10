/**
 * Pure snapping math for the cellbuilder — no three.js, node-testable.
 *
 * Cells are axis-aligned boxes {origin (min corner), size}. "Magnetic"
 * behaviour = translate a candidate box by the smallest corner-to-corner
 * delta against the existing cell corners when within the snap threshold.
 */

export type Vec3 = [number, number, number];

export interface CellBox {
    origin: Vec3;
    size: Vec3;
}

/** Quantize a scalar to the grid step (step<=0 -> unchanged). Rounds away
 * float dust (0.1 * 12 = 1.2000000000000002) so committed docs stay clean. */
export function quantize(v: number, step: number): number {
    if (step <= 0) return v;
    return Math.round((Math.round(v / step) * step) * 1e9) / 1e9;
}

export function quantizeVec(v: Vec3, step: number): Vec3 {
    return [quantize(v[0], step), quantize(v[1], step), quantize(v[2], step)];
}

/** The 8 corners of a box. */
export function boxCorners(box: CellBox): Vec3[] {
    const [x, y, z] = box.origin;
    const [dx, dy, dz] = box.size;
    const out: Vec3[] = [];
    for (const cx of [x, x + dx]) {
        for (const cy of [y, y + dy]) {
            for (const cz of [z, z + dz]) {
                out.push([cx, cy, cz]);
            }
        }
    }
    return out;
}

function norm(v: Vec3): number {
    return Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
}

/**
 * Vertex magnetism: the smallest delta that moves one of the candidate's
 * corners exactly onto one of the existing corners, or null when no pair is
 * within `threshold`.
 */
export function snapToVertices(candidateCorners: Vec3[], existingCorners: Vec3[], threshold: number): Vec3 | null {
    let best: Vec3 | null = null;
    let bestDist = threshold;
    for (const c of candidateCorners) {
        for (const e of existingCorners) {
            const delta: Vec3 = [e[0] - c[0], e[1] - c[1], e[2] - c[2]];
            const d = norm(delta);
            if (d <= bestDist) {
                bestDist = d;
                best = delta;
            }
        }
    }
    return best;
}

// NOTE: the cell translate gizmo's vertex snapping lives in the controller
// (CellBuilderController), not here — it's pointer-driven (nearest neighbour
// vertex to the cursor in screen space) and needs the camera/projection, so it
// can't be a pure function. `snapToVertices` / `snapBox` below stay pure and
// drive the ADD-mode magnetism (dropping a fresh box onto existing corners).

/** Convenience: snap a whole candidate box against a set of existing boxes. */
export function snapBox(candidate: CellBox, existing: CellBox[], threshold: number): CellBox {
    if (!existing.length) return candidate;
    const corners = existing.flatMap(boxCorners);
    const delta = snapToVertices(boxCorners(candidate), corners, threshold);
    if (delta === null) return candidate;
    return {
        origin: [candidate.origin[0] + delta[0], candidate.origin[1] + delta[1], candidate.origin[2] + delta[2]],
        size: candidate.size,
    };
}

/**
 * BoxGeometry face bookkeeping. three.js BoxGeometry emits 6 groups whose
 * materialIndex order is +X, -X, +Y, -Y, +Z, -Z. TopoSpace's side-exclusion
 * fields (SE0..SE5) use the ada.topology convention BOTTOM(-Z)=0, TOP(+Z)=1,
 * FRONT(-Y)=2, BACK(+Y)=3, LEFT(-X)=4, RIGHT(+X)=5.
 */
export interface FaceSide {
    axis: 0 | 1 | 2;
    positive: boolean;
    /** TopoSpace side-exclusion index (the N of SE{N}). */
    se: number;
    label: string;
}

export const BOX_FACE_SIDES: readonly FaceSide[] = [
    {axis: 0, positive: true, se: 5, label: "+X"},
    {axis: 0, positive: false, se: 4, label: "-X"},
    {axis: 1, positive: true, se: 3, label: "+Y"},
    {axis: 1, positive: false, se: 2, label: "-Y"},
    {axis: 2, positive: true, se: 1, label: "+Z"},
    {axis: 2, positive: false, se: 0, label: "-Z"},
];

const AXIS_LABEL = ["X", "Y", "Z"] as const;

export function axisLabel(axis: 0 | 1 | 2): string {
    return AXIS_LABEL[axis];
}

/** A border edge of a box face: it runs along `axis` and bounds
 * `boundaryAxis` at its low or high side (`boundaryPositive`). */
export interface EdgeHit {
    axis: 0 | 1 | 2;
    boundaryAxis: 0 | 1 | 2;
    boundaryPositive: boolean;
}

/**
 * Edge detection for a click landing on a box face: when the hit point runs
 * close (within `tol`) to one of the face's 4 border edges, return that edge
 * (its length = box.size[axis]). Corners resolve to the nearest single
 * border; null means the click was in the face interior.
 */
export function edgeHitOnFace(
    box: CellBox,
    faceMaterialIndex: number,
    point: Vec3,
    tol: number,
): EdgeHit | null {
    const side = BOX_FACE_SIDES[faceMaterialIndex];
    if (!side) return null;
    const inPlane = ([0, 1, 2] as const).filter((a) => a !== side.axis) as [0 | 1 | 2, 0 | 1 | 2];
    let best: (EdgeHit & {dist: number}) | null = null;
    for (const boundaryAxis of inPlane) {
        // the edge that bounds `boundaryAxis` runs along the OTHER in-plane axis
        const runAxis = inPlane[0] === boundaryAxis ? inPlane[1] : inPlane[0];
        const lo = box.origin[boundaryAxis];
        const hi = lo + box.size[boundaryAxis];
        const dLo = Math.abs(point[boundaryAxis] - lo);
        const dHi = Math.abs(point[boundaryAxis] - hi);
        const dist = Math.min(dLo, dHi);
        if (dist <= tol && (best === null || dist < best.dist)) {
            best = {axis: runAxis, boundaryAxis, boundaryPositive: dHi < dLo, dist};
        }
    }
    return best ? {axis: best.axis, boundaryAxis: best.boundaryAxis, boundaryPositive: best.boundaryPositive} : null;
}

/** World-space endpoints (in the box's coordinate frame) of a face border
 * edge, derived from the current box so they stay valid through resizes. */
export function edgeEndpoints(box: CellBox, faceMaterialIndex: number, edge: EdgeHit): {start: Vec3; end: Vec3} {
    const side = BOX_FACE_SIDES[faceMaterialIndex];
    const base: Vec3 = [...box.origin];
    if (side.positive) base[side.axis] += box.size[side.axis];
    if (edge.boundaryPositive) base[edge.boundaryAxis] += box.size[edge.boundaryAxis];
    const end: Vec3 = [...base];
    end[edge.axis] += box.size[edge.axis];
    return {start: base, end};
}

/** World-space centre of a box face (in the box's own frame). Used to place
 * the resize gizmo's face handles. Falls back to the box centre for an
 * out-of-range face index. */
export function faceCenter(box: CellBox, faceMaterialIndex: number): Vec3 {
    const c: Vec3 = [
        box.origin[0] + box.size[0] / 2,
        box.origin[1] + box.size[1] / 2,
        box.origin[2] + box.size[2] / 2,
    ];
    const side = BOX_FACE_SIDES[faceMaterialIndex];
    if (side) c[side.axis] = box.origin[side.axis] + (side.positive ? box.size[side.axis] : 0);
    return c;
}

/** Origin (min corner) that centres a box of `size` on `center`, grid-quantized.
 * The inverse of the centre computed for a mesh — used to map a translate
 * gizmo's dragged centre back to the cell's stored origin. */
export function originFromCenter(center: Vec3, size: Vec3, step: number): Vec3 {
    return [
        quantize(center[0] - size[0] / 2, step),
        quantize(center[1] - size[1] / 2, step),
        quantize(center[2] - size[2] / 2, step),
    ];
}

/** Which horizontal surface of a cell equipment seats against. */
export type CellSurface = "floor" | "roof";
/** Which side of that surface the equipment sits on: TOP = box above the
 * surface (rests on it), BOTTOM = box below it (hangs under it). */
export type CellSide = "top" | "bottom";

/**
 * Origin (min corner) for equipment seated onto/into a cell. X/Y centre the
 * box on the cell footprint; Z follows the chosen surface (floor = cell base,
 * roof = cell top) and side (top = box sits above the surface, bottom = box
 * hangs below it). The four combinations cover the "into / onto" placements:
 *   roof+top    → onto the cell (sits on the ceiling)
 *   roof+bottom → into the cell, hung from the ceiling
 *   floor+top   → into the cell, standing on the floor
 *   floor+bottom→ under the cell, hung below the floor
 * All grid-quantized.
 */
export function placeInCell(
    cell: CellBox,
    size: Vec3,
    surface: CellSurface,
    side: CellSide,
    step: number,
): Vec3 {
    const surfaceZ = surface === "floor" ? cell.origin[2] : cell.origin[2] + cell.size[2];
    const z = side === "top" ? surfaceZ : surfaceZ - size[2];
    return [
        quantize(cell.origin[0] + cell.size[0] / 2 - size[0] / 2, step),
        quantize(cell.origin[1] + cell.size[1] / 2 - size[1] / 2, step),
        quantize(z, step),
    ];
}

/** Resize the box along one axis to `length`, keeping the origin fixed. */
export function withAxisLength(box: CellBox, axis: 0 | 1 | 2, length: number, minSize = 0.1): CellBox {
    const size: Vec3 = [...box.size];
    size[axis] = Math.max(minSize, length);
    return {origin: [...box.origin], size};
}

/**
 * Face drag: apply a signed offset along one axis face of a box. Positive
 * faces grow/shrink size; negative faces move the origin and counter-adjust
 * size, so the opposite face stays put. Size is clamped to >= minSize.
 */
export function applyFaceOffset(box: CellBox, axis: 0 | 1 | 2, positiveFace: boolean, offset: number, minSize = 0.1): CellBox {
    const origin: Vec3 = [...box.origin];
    const size: Vec3 = [...box.size];
    if (positiveFace) {
        size[axis] = Math.max(minSize, size[axis] + offset);
    } else {
        const applied = Math.min(offset, size[axis] - minSize);
        origin[axis] += applied;
        size[axis] -= applied;
    }
    return {origin, size};
}

// --- Keyboard extrude + selection-cycle geometry (pure, node-testable) ------

/**
 * The box a keyboard "extrude" grows from a selected face: a NEW cell adjacent
 * to `faceIndex`, sharing that face's cross-section (same origin/size in the two
 * axes orthogonal to the face) and `depth` metres deep along the face axis.
 * A positive `depth` grows outward from the face; a negative `depth` flips the
 * growth to the inward direction (leading-`-` numeric entry). |depth| is the
 * new cell's size along the axis; the origin is the min corner either way.
 */
export function extrudeBox(box: CellBox, faceIndex: number, depth: number): CellBox {
    const side = BOX_FACE_SIDES[faceIndex];
    const origin: Vec3 = [...box.origin];
    const size: Vec3 = [...box.size];
    if (!side) return {origin, size};
    const a = side.axis;
    // Coordinate of the extruded face plane and the outward unit direction.
    const facePos = side.positive ? box.origin[a] + box.size[a] : box.origin[a];
    const outward = side.positive ? 1 : -1;
    const far = facePos + outward * depth; // depth<0 flips to the inward side
    origin[a] = Math.min(facePos, far);
    size[a] = Math.abs(depth);
    return {origin, size};
}

/**
 * After an extrude, the face of the NEW cell to auto-select so a repeated
 * extrude keeps growing in the same visual direction: the face pointing along
 * the growth. Positive depth grows on the source face's side (same index);
 * a flipped (negative) depth grows on the opposite side (index ^ 1).
 */
export function farFaceAfterExtrude(faceIndex: number, depth: number): number {
    return depth >= 0 ? faceIndex : faceIndex ^ 1;
}

/** Next/previous BoxGeometry face material index, wrapping 0..5. */
export function cycleFaceIndex(current: number, dir: 1 | -1): number {
    return (((current + dir) % 6) + 6) % 6;
}

/**
 * The four border edges of a box face, in a stable order (for keyboard edge
 * cycling). Order: the two edges bounding the first in-plane axis (low, high),
 * then the two bounding the second — each `EdgeHit` runs along the OTHER
 * in-plane axis, matching `edgeHitOnFace`'s descriptor.
 */
export function faceEdges(faceMaterialIndex: number): EdgeHit[] {
    const side = BOX_FACE_SIDES[faceMaterialIndex];
    if (!side) return [];
    const inPlane = ([0, 1, 2] as const).filter((a) => a !== side.axis) as [0 | 1 | 2, 0 | 1 | 2];
    const out: EdgeHit[] = [];
    for (const boundaryAxis of inPlane) {
        const runAxis = inPlane[0] === boundaryAxis ? inPlane[1] : inPlane[0];
        for (const boundaryPositive of [false, true]) {
            out.push({axis: runAxis, boundaryAxis, boundaryPositive});
        }
    }
    return out;
}

/** Index (0..3) of `edge` within `faceEdges(faceIndex)`, or -1 if not found. */
export function edgeIndexInFace(faceMaterialIndex: number, edge: EdgeHit): number {
    return faceEdges(faceMaterialIndex).findIndex(
        (e) =>
            e.axis === edge.axis &&
            e.boundaryAxis === edge.boundaryAxis &&
            e.boundaryPositive === edge.boundaryPositive,
    );
}
