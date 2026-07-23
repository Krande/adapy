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

/**
 * Edge detection for a click landing on a box face: when the hit point runs
 * close (within `tol`) to one of the face's 4 border edges, return the axis
 * that edge runs along (its length = box.size[axis]). Corners resolve to the
 * nearest single border; null means the click was in the face interior.
 */
export function edgeHitOnFace(
    box: CellBox,
    faceMaterialIndex: number,
    point: Vec3,
    tol: number,
): {axis: 0 | 1 | 2} | null {
    const side = BOX_FACE_SIDES[faceMaterialIndex];
    if (!side) return null;
    const inPlane = ([0, 1, 2] as const).filter((a) => a !== side.axis) as [0 | 1 | 2, 0 | 1 | 2];
    let best: {axis: 0 | 1 | 2; dist: number} | null = null;
    for (const boundaryAxis of inPlane) {
        // the edge that bounds `boundaryAxis` runs along the OTHER in-plane axis
        const runAxis = inPlane[0] === boundaryAxis ? inPlane[1] : inPlane[0];
        const lo = box.origin[boundaryAxis];
        const hi = lo + box.size[boundaryAxis];
        const dist = Math.min(Math.abs(point[boundaryAxis] - lo), Math.abs(point[boundaryAxis] - hi));
        if (dist <= tol && (best === null || dist < best.dist)) {
            best = {axis: runAxis, dist};
        }
    }
    return best ? {axis: best.axis} : null;
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
