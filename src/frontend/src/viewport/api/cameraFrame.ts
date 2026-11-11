// The camera rules every viewport shares, as plain numbers: where an empty scene is seen from, how a
// box is framed, and the clip range for content of a given size. No three.js.

import type {Box6, Vec3} from "./sceneFrame";

/** The view of an empty scene. */
export const DEFAULT_VIEW = {
    eye: [-5, 5, 5] as Vec3,
    target: [0, 0, 0] as Vec3,
    fovDeg: 60,
    near: 0.1,
    far: 10000,
} as const;

/** The sphere around a box: its centre, and half its diagonal. */
export function boundingSphere(box: Box6): {center: Vec3; radius: number} {
    const dx = box[3] - box[0], dy = box[4] - box[1], dz = box[5] - box[2];
    return {
        center: [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2],
        radius: Math.hypot(dx, dy, dz) / 2,
    };
}

/** Near and far for content `radius` across: zooming in stays clear of the near plane at any scale,
 *  with a near/far ratio of about 1e6. Null for a radius that is not a positive number. */
export function clipRangeFor(radius: number): {near: number; far: number} | null {
    if (!isFinite(radius) || radius <= 0) return null;
    const near = Math.max(radius * 1e-3, 1e-6);
    return {near, far: Math.max(radius * 1e3, near * 1e4)};
}

function fieldsOfView(fovDeg: number, aspect: number): {v: number; h: number} {
    const v = (fovDeg * Math.PI) / 180;
    return {v, h: 2 * Math.atan(Math.tan(v / 2) * (aspect || 1))};
}

/** The distance at which a sphere's outline, at its centre's depth, meets the narrower edge of the view. */
export function sphereFitDistance(radius: number, fovDeg: number, aspect: number): number {
    const {v, h} = fieldsOfView(fovDeg, aspect);
    return Math.max(radius / Math.tan(v / 2), radius / Math.tan(h / 2));
}

/** The distance at which the WHOLE sphere is in view both ways (its silhouette inside the narrower
 *  field of view): farther than `sphereFitDistance`, so geometry nearer than the centre stays in. */
export function wholeSphereDistance(radius: number, fovDeg: number, aspect: number): number {
    const {v, h} = fieldsOfView(fovDeg, aspect);
    return radius / Math.sin(Math.min(v, h) / 2);
}

/** Frame `box` looking along `direction` (unit, from the camera towards what it sees): the target at the
 *  box's centre, the camera `sphereFitDistance` x `scale` back from it, for a sphere no smaller than
 *  `minRadius`. Null for an empty box or a box of no size. */
export function framePose(
    box: Box6,
    direction: ArrayLike<number>,
    fovDeg: number,
    aspect: number,
    scale = 1,
    minRadius = 0,
): {eye: Vec3; target: Vec3; radius: number} | null {
    if (!(box[0] <= box[3] && box[1] <= box[4] && box[2] <= box[5])) return null;
    const sphere = boundingSphere(box);
    if (!isFinite(sphere.radius) || sphere.radius === 0) return null;
    const radius = Math.max(sphere.radius, minRadius);
    return {eye: along(sphere.center, direction, sphereFitDistance(radius, fovDeg, aspect) * scale), target: sphere.center, radius};
}

/** Frame a selection's box looking along `direction`: the camera `radius / sin(fov / 2) / fillFactor`
 *  back from the box's centre (the vertical field of view). Null for an empty box or one of no size. */
export function selectionFramePose(
    box: Box6,
    direction: ArrayLike<number>,
    fovDeg: number,
    fillFactor = 1,
): {eye: Vec3; target: Vec3; radius: number} | null {
    if (!(box[0] <= box[3] && box[1] <= box[4] && box[2] <= box[5])) return null;
    const {center, radius} = boundingSphere(box);
    if (!(radius > 0)) return null;
    const distance = radius / Math.sin((fovDeg * Math.PI) / 360) / fillFactor;
    return {eye: along(center, direction, distance), target: center, radius};
}

/** A little room around a named view: a sphere's outline is the model's at its corners only. */
export const NAMED_VIEW_MARGIN = 1.1;

/** Look at `box` from `towardCamera`'s side (unit, from what is seen to the camera): the whole sphere in
 *  view with a margin. With no box, turn about the current target at the current distance. */
export function namedViewPose(
    box: Box6 | null,
    towardCamera: ArrayLike<number>,
    fovDeg: number,
    aspect: number,
    current: {eye: ArrayLike<number>; target: ArrayLike<number>},
): {eye: Vec3; target: Vec3; radius: number | null} {
    const back: Vec3 = [-towardCamera[0], -towardCamera[1], -towardCamera[2]];
    if (box) {
        const sphere = boundingSphere(box);
        const radius = Math.max(sphere.radius, 1e-4);
        return {eye: along(sphere.center, back, wholeSphereDistance(radius, fovDeg, aspect) * NAMED_VIEW_MARGIN), target: sphere.center, radius};
    }
    const t = current.target, e = current.eye;
    const target: Vec3 = [t[0], t[1], t[2]];
    const distance = Math.max(Math.hypot(e[0] - t[0], e[1] - t[1], e[2] - t[2]), 1e-3);
    return {eye: along(target, back, distance), target, radius: null};
}

/** `distance` back from `center`, against `direction`. */
function along(center: Vec3, direction: ArrayLike<number>, distance: number): Vec3 {
    return [center[0] - direction[0] * distance, center[1] - direction[1] * distance, center[2] - direction[2] * distance];
}
