/**
 * A CAMERA placed in the scene, and the handles that turn it: plain numbers.
 *
 * Owns: a camera's look direction as a turn about Z (yaw) and a tilt up or
 * down (pitch); the body, the lens and the arrow to its target that a
 * viewport draws for it; and the two rings its turn gizmo is made of -- the
 * yaw ring flat on the floor's plane and the pitch ring standing in the
 * vertical plane the camera looks along -- with what a pointer's travel on one
 * of them is as a turn. The move arrows are the box's (./boxPose.ts).
 *
 * As the box's: found and dragged on screen, through a viewport's word on
 * where a point of the world is in the view, so one set of maths serves both
 * viewports. No three.js in this module.
 */

import type {Vec3} from "./boxPose";

/** Where a camera stands and what it looks at, in the scene's own coordinates. */
export interface CameraPose {
    position: Vec3;
    target: Vec3;
}

/** Pitch is kept this far off straight up and straight down: a camera looking along Z has no yaw. */
export const MAX_PITCH_DEG = 89;

const DEG = Math.PI / 180;
const sub = (a: readonly number[], b: readonly number[]): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const len = (a: readonly number[]): number => Math.hypot(a[0], a[1], a[2]);
const unit = (a: readonly number[]): Vec3 => {
    const n = len(a);
    return n > 1e-12 ? [a[0] / n, a[1] / n, a[2] / n] : [0, 1, 0];
};
const cross = (a: readonly number[], b: readonly number[]): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const along = (p: readonly number[], ...terms: [number, readonly number[]][]): Vec3 =>
    [0, 1, 2].map((k) => terms.reduce((s, [t, d]) => s + t * d[k], p[k])) as Vec3;

/** How far the camera is from its target; the least distance there is when it stands on it. */
export function cameraDistance(pose: CameraPose): number {
    return Math.max(len(sub(pose.target, pose.position)), 1e-6);
}

/** The camera's look direction (a unit vector, towards the target); +Y for a camera on its target. */
export function lookDirection(pose: CameraPose): Vec3 {
    return unit(sub(pose.target, pose.position));
}

/** The look direction as a turn about Z from +X, anticlockwise seen from above, and a tilt above
 *  the floor's plane (negative looking down), in degrees. */
export function yawPitchOf(direction: readonly number[]): {yawDeg: number; pitchDeg: number} {
    const d = unit(direction);
    const flat = Math.hypot(d[0], d[1]);
    return {yawDeg: flat > 1e-9 ? Math.atan2(d[1], d[0]) / DEG : 0, pitchDeg: Math.atan2(d[2], flat) / DEG};
}

/** The unit look direction of a yaw and a pitch (degrees); the pitch kept within `MAX_PITCH_DEG`. */
export function directionOf(yawDeg: number, pitchDeg: number): Vec3 {
    const p = Math.max(-MAX_PITCH_DEG, Math.min(MAX_PITCH_DEG, pitchDeg)) * DEG, y = yawDeg * DEG;
    return [Math.cos(p) * Math.cos(y), Math.cos(p) * Math.sin(y), Math.sin(p)];
}

/** The camera turned to look along yaw and pitch, from where it stands: its target as far from it as
 *  it was, the other way round from before. */
export function turnedCamera(pose: CameraPose, yawDeg: number, pitchDeg: number): CameraPose {
    const d = directionOf(yawDeg, pitchDeg);
    const r = cameraDistance(pose);
    const round = (v: number) => Math.round(v * 1e6) / 1e6 || 0;
    return {position: [...pose.position] as Vec3, target: along(pose.position, [r, d]).map(round) as Vec3};
}

/** The camera's own frame: its right, its up (the scene's Z as far as it can be) and its look direction. */
export function cameraBasis(pose: CameraPose): {right: Vec3; up: Vec3; forward: Vec3} {
    const forward = lookDirection(pose);
    let right = cross(forward, [0, 0, 1]);
    if (len(right) < 1e-9) right = [1, 0, 0];
    right = unit(right);
    return {right, up: cross(right, forward), forward};
}

/** The camera's body as a box in its own frame (right, forward, up), for `size` the body's length:
 *  its eight corners in the scene, in the order `boxCorners` gives a box's (bit 0 right, bit 1
 *  forward, bit 2 up), so a box's face triangles fill it. The lens is at the body's front, on the camera's position. */
export function cameraBodyCorners(pose: CameraPose, size: number): Float32Array {
    const {right, up, forward} = cameraBasis(pose);
    const out = new Float32Array(24);
    const w = 0.3 * size, h = 0.22 * size;
    for (let i = 0; i < 8; i++) {
        out.set(along(pose.position, [i & 1 ? w : -w, right], [i & 2 ? -0.05 * size : -size, forward], [i & 4 ? h : -h, up]), i * 3);
    }
    return out;
}

/** The camera's lines: its body's twelve edges, the lens (four lines from the position to a frame in
 *  front of it, and the frame) and a mark on the frame's top edge, which way is up. */
export function cameraBodySegments(pose: CameraPose, size: number): Float32Array {
    const corners = cameraBodyCorners(pose, size);
    const at = (i: number) => Array.from(corners.subarray(i * 3, i * 3 + 3));
    const edges = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
    const {right, up, forward} = cameraBasis(pose);
    const o = pose.position;
    const front = along(o, [0.6 * size, forward]);
    const frame = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => along(front, [a * 0.45 * size, right], [b * 0.33 * size, up]));
    const mark = [along(front, [-0.15 * size, right], [0.33 * size, up]), along(front, [0.47 * size, up]), along(front, [0.15 * size, right], [0.33 * size, up])];
    const segments: number[][] = [
        ...edges.map(([a, b]) => [...at(a), ...at(b)]),
        ...frame.map((p) => [...o, ...p]),
        ...frame.map((p, i) => [...p, ...frame[(i + 1) % 4]]),
        [...mark[0], ...mark[1]],
        [...mark[1], ...mark[2]],
    ];
    return new Float32Array(segments.flat());
}

/** The arrow from the camera to its target: a shaft and four barbs at the target (five segments). */
export function cameraArrowSegments(pose: CameraPose, size: number): Float32Array {
    const {right, up, forward} = cameraBasis(pose);
    const t = pose.target;
    const head = Math.min(0.5 * size, 0.3 * cameraDistance(pose));
    const barb = (side: readonly number[], s: number) => [...t, ...along(t, [-head, forward], [0.4 * head * s, side])];
    return new Float32Array([...pose.position, ...t, ...barb(right, 1), ...barb(right, -1), ...barb(up, 1), ...barb(up, -1)]);
}

export const CAMERA_RING_SIDES = 48;

/** The pitch ring's points: a circle about the camera's right through its position, standing in the
 *  vertical plane of the look direction. Point `i` is `i` steps round from straight ahead, upwards. */
export function pitchRingPoints(pose: CameraPose, radius: number, sides = CAMERA_RING_SIDES): Vec3[] {
    const ahead = aheadOf(pose);
    const out: Vec3[] = [];
    for (let i = 0; i < sides; i++) {
        const a = (i / sides) * 2 * Math.PI;
        out.push(along(pose.position, [radius * Math.cos(a), ahead], [radius * Math.sin(a), [0, 0, 1]]));
    }
    return out;
}

/** The horizontal unit vector the camera looks along, seen from above: the pitch ring's plane is it and Z. */
export function aheadOf(pose: CameraPose): Vec3 {
    const {yawDeg} = yawPitchOf(lookDirection(pose));
    return [Math.cos(yawDeg * DEG), Math.sin(yawDeg * DEG), 0];
}
