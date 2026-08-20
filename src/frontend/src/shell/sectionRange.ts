// Where a section plane sits, and how far its slider may travel.
//
// Extracted from SectionPlanesPanel when the Clip tab was folded into the toolbar. Pure
// on purpose: it is the only part of clipping with arithmetic worth checking, and the
// component it came from reaches stores that reach the model worker.

export interface Bounds {
    min: {x: number; y: number; z: number};
    max: {x: number; y: number; z: number};
}

/**
 * Signed distance from the origin to the plane, along its own normal — the natural thing
 * for a slider to hold.
 *
 * A three.js plane stores `constant = -(normal · point)`, so for a unit normal the
 * position is simply its negation. Keeping the conversion here (rather than inline at two
 * call sites with opposite signs) is what stops a slider that moves the plane backwards.
 */
export function planePosition(constant: number): number {
    return -constant;
}

/** The inverse: what to store for a slider that reads `position`. */
export function planeConstant(position: number): number {
    return -position;
}

/**
 * Slider travel for a plane: the model's bounding box projected onto the plane normal,
 * padded 10% at each end.
 *
 * The padding is the point. Without it the extremes of the slider leave the plane exactly
 * touching the bounding box, so you can never fully reveal or fully clip the model — the
 * last sliver stays, and it reads as the slider being broken rather than as a range that
 * stops one pixel short. The range stays symmetric about the box centre, so a plane added
 * at the centre sits at the slider midpoint.
 */
export function sliderRange(normal: [number, number, number], bb: Bounds | null): [number, number] {
    if (!bb) return [-100, 100];
    const [nx, ny, nz] = normal;
    const xs = [bb.min.x, bb.max.x];
    const ys = [bb.min.y, bb.max.y];
    const zs = [bb.min.z, bb.max.z];
    const projections: number[] = [];
    for (const x of xs) for (const y of ys) for (const z of zs) projections.push(nx * x + ny * y + nz * z);
    const lo = Math.min(...projections);
    const hi = Math.max(...projections);
    const pad = (hi - lo) * 0.1 || 1;
    return [lo - pad, hi + pad];
}

/** Slider step: 1/500 of the travel, never zero — a zero step freezes the input. */
export function sliderStep(lo: number, hi: number): number {
    return (hi - lo) / 500 || 0.01;
}
