// Where a section plane sits, as the section controls show it: a position on the plane's axis in the
// MODEL's own coordinates, with the model's extent on that axis as the range. The store keeps planes
// in world coordinates (state/sectionStore: normal . x + constant = 0), and a viewport re-centres what
// it loads (world = model + offset, state/sceneFrameStore), so a cut entered as "z = 12.5" must be
// moved by the offset. The position is along the axis, not along the normal: flipping a plane (which
// negates both) keeps its position and range. One representation for every control - the section
// strip (layout/ModeToolbars) and the classic Clip panel - and no three.js.

import type {SceneOffset} from "@/state/sceneFrameStore";

/** The model's box in the world: min x, y, z, max x, y, z (a viewport's `modelBounds()`). */
export type SectionBounds = readonly [number, number, number, number, number, number];

/** How far past the model the range runs at either end, as a share of its extent: sliding to an end
 *  moves the plane clear of the model, all of it shown at one end and all of it cut at the other. */
export const SECTION_RANGE_PAD = 0.1;

export interface SectionAxis {
    /** The axis the plane cuts (its normal's largest component): 0 x, 1 y, 2 z. */
    axis: 0 | 1 | 2;
    /** Which way along it the normal points. */
    sign: 1 | -1;
    /** Where the plane is on the axis, in model coordinates. */
    position: number;
    /** The range to offer, in model coordinates: the model's extent, padded. -1 to 1 with no model. */
    lo: number;
    hi: number;
}

/**
 * Where `plane` is on its axis and the range to offer, in model coordinates. With no plane the Z
 * axis at 0; with no offset published the world is the model's frame.
 */
export function sectionAxis(
    plane: {normal: readonly number[]; constant: number} | undefined,
    bounds: SectionBounds | null,
    offset: SceneOffset | null = null,
): SectionAxis {
    const n = plane?.normal ?? [0, 0, 1];
    const axis = n.reduce((best, v, i) => (Math.abs(v) > Math.abs(n[best]) ? i : best), 0) as 0 | 1 | 2;
    const sign = n[axis] < 0 ? -1 : 1;
    const shift = offset?.[axis] ?? 0;
    // normal . x + constant = 0 with the normal on the axis: the plane is at x[axis] = -constant / sign.
    const position = plane ? -plane.constant * sign - shift : 0;
    if (!bounds) return {axis, sign, position, lo: -1, hi: 1};
    const lo = bounds[axis] - shift;
    const hi = bounds[axis + 3] - shift;
    const pad = (hi - lo) * SECTION_RANGE_PAD || 1;
    return {axis, sign, position, lo: lo - pad, hi: hi + pad};
}

/** The world constant that puts a plane facing `sign` along `axis` at model `position`. */
export function sectionConstant(at: Pick<SectionAxis, "axis" | "sign">, position: number, offset: SceneOffset | null = null): number {
    return -at.sign * (position + (offset?.[at.axis] ?? 0));
}
