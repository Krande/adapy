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

// The orthographic view (utils/scene/orthographicProjection.ts, and adacpp's camera on the
// three.js-free viewport) is as tall at the target as the perspective view: half its height is
// distance * tan(fov / 2). So it is sized by where the camera stands, and a fit that works out a
// perspective distance frames loosely there: a sphere round the box, seen along the box's longest
// diagonal, is wider than the box looks from most directions. An orthographic fit sizes the view
// from the box as it lies on the view plane instead, and then stands the camera at the distance
// that gives that height.

/** The room an orthographic fit leaves round the box's outline: adacpp's default fit margin. */
export const ORTHO_FIT_MARGIN = 1.15;

/** The room an orthographic fit of a box (a face, a selection: `frameBox`, a viewport's `fitBox`)
 *  leaves round its outline. A little more than the model's, so the target reads as framed rather
 *  than cropped; not the perspective fit's extra distance (1.6), which is room for a sphere seen
 *  from its nearest side and would leave the outline at little more than half the view. */
export const ORTHO_FIT_BOX_MARGIN = 1.3;

const sub3 = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot3 = (a: ArrayLike<number>, b: ArrayLike<number>): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross3 = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** The screen's right and up in the world, for a camera looking along `direction` with `upHint`
 *  (its up on screen, or the world's up axis) deciding its roll; null when the two are parallel. */
export function screenAxes(direction: ArrayLike<number>, upHint: ArrayLike<number>): {right: Vec3; up: Vec3} | null {
    const right = cross3(direction, upHint);
    const l = Math.hypot(right[0], right[1], right[2]);
    if (!(l > 1e-9)) return null;
    const r: Vec3 = [right[0] / l, right[1] / l, right[2] / l];
    const up = cross3(r, direction);
    const u = Math.hypot(up[0], up[1], up[2]) || 1;
    return {right: r, up: [up[0] / u, up[1] / u, up[2] / u]};
}

/** Half the width and half the height `box` covers on the view plane, about its centre. With no
 *  screen axes (a view along `upHint`), the box's widest extent across the view, both ways. */
export function projectedHalfExtents(box: Box6, direction: ArrayLike<number>, upHint: ArrayLike<number>): {x: number; y: number} {
    const c: Vec3 = [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2];
    const axes = screenAxes(direction, upHint);
    let x = 0, y = 0;
    for (let i = 0; i < 8; i++) {
        const corner = sub3([i & 1 ? box[3] : box[0], i & 2 ? box[4] : box[1], i & 4 ? box[5] : box[2]], c);
        if (axes) {
            x = Math.max(x, Math.abs(dot3(corner, axes.right)));
            y = Math.max(y, Math.abs(dot3(corner, axes.up)));
        } else {
            const along = dot3(corner, direction);
            const across = Math.sqrt(Math.max(0, dot3(corner, corner) - along * along));
            x = y = Math.max(x, across);
        }
    }
    return {x, y};
}

/** Frame `box` in an orthographic view looking along `direction` (unit, from the camera towards what
 *  it sees), `upHint` deciding the roll: the target at the box's centre, the view's half-height that
 *  of the box's outline on the view plane (the narrower way decides) times `margin`, and the camera
 *  at the distance that gives that half-height (distance * tan(fov / 2) / zoom). `minHalfHeight`
 *  keeps a tiny box from filling the view. `radius` is the box's sphere, for the clip range. Null for
 *  an empty box or a box of no size. */
export function orthoFramePose(
    box: Box6,
    direction: ArrayLike<number>,
    upHint: ArrayLike<number>,
    fovDeg: number,
    aspect: number,
    margin = ORTHO_FIT_MARGIN,
    minHalfHeight = 0,
    zoom = 1,
): {eye: Vec3; target: Vec3; radius: number; halfHeight: number} | null {
    if (!(box[0] <= box[3] && box[1] <= box[4] && box[2] <= box[5])) return null;
    const {center, radius} = boundingSphere(box);
    if (!(radius > 0)) return null;
    const half = projectedHalfExtents(box, direction, upHint);
    const halfHeight = Math.max(Math.max(half.y, half.x / Math.max(aspect || 1, 1e-3)) * margin, minHalfHeight, 1e-6);
    const distance = (halfHeight * (zoom || 1)) / Math.tan((fovDeg * Math.PI) / 360);
    return {eye: along(center, direction, distance), target: center, radius, halfHeight};
}

/** `distance` back from `center`, against `direction`. */
function along(center: Vec3, direction: ArrayLike<number>, distance: number): Vec3 {
    return [center[0] - direction[0] * distance, center[1] - direction[1] * distance, center[2] - direction[2] * distance];
}
