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
