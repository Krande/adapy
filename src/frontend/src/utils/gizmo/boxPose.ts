/**
 * A rigid BOX placed in the scene, and the handles that move it: plain numbers.
 *
 * Owns: where a box stands for a pose (its position and its turn about Z), as
 * the edges and faces a viewport draws; the move arrows, the plane square and
 * the turn ring drawn at its position, and the resize handles on its faces; and
 * what a pointer's travel on screen is as a move along an axis, a move in the
 * floor's plane, a turn or a face moved, with the snap steps the transform
 * gizmo uses (./transformKeys.ts).
 *
 * The handles are found and dragged on screen: a viewport says where a point of
 * the world is in the view, and nothing else is asked of it, so one set of
 * maths serves both viewports. No three.js in this module.
 */

import type {GizmoAxis} from "./transformKeys";

export type Vec3 = [number, number, number];
/** A point of the view, in CSS pixels from its top-left corner. */
export interface ScreenPoint {
    x: number;
    y: number;
}

/** Where a box stands: its own origin in the scene, and its turn about Z (anticlockwise seen from
 *  above). A box that turns every way has `axes` too: its own x, y and z in the scene (unit vectors,
 *  at right angles, right-handed), which then say how it is turned, `yawDeg` only where its x heads. */
export interface BoxPose {
    position: Vec3;
    yawDeg: number;
    axes?: BoxAxes;
}

/** A box's own x, y and z axes in the scene. */
export type BoxAxes = [Vec3, Vec3, Vec3];

/** A box in its own frame: min xyz, max xyz. Its origin (0, 0, 0) is what a pose places and turns about. */
export type LocalBox = readonly [number, number, number, number, number, number];

const AXES: readonly Vec3[] = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
];

/** The unit vector of a world axis. */
export function axisVector(axis: GizmoAxis): Vec3 {
    return [...AXES[axis]] as Vec3;
}

/** A point of the box's own frame where the pose puts it. */
export function poseToScene(pose: BoxPose, local: readonly number[]): Vec3 {
    const axes = pose.axes;
    if (axes) {
        const p = pose.position;
        return [0, 1, 2].map((k) => p[k] + axes[0][k] * local[0] + axes[1][k] * local[1] + axes[2][k] * local[2]) as Vec3;
    }
    const yaw = (pose.yawDeg * Math.PI) / 180;
    const c = Math.cos(yaw), s = Math.sin(yaw);
    return [pose.position[0] + c * local[0] - s * local[1], pose.position[1] + s * local[0] + c * local[1], pose.position[2] + local[2]];
}

/** The box's eight corners (three numbers each): corner `i` is at max x for bit 0, max y for bit 1, max z for bit 2. */
export function boxCorners(box: LocalBox, pose: BoxPose, out = new Float32Array(24)): Float32Array {
    for (let i = 0; i < 8; i++) {
        const p = poseToScene(pose, [box[i & 1 ? 3 : 0], box[i & 2 ? 4 : 1], box[i & 4 ? 5 : 2]]);
        out.set(p, i * 3);
    }
    return out;
}

/** The corners each of the box's twelve edges joins. */
const EDGES: readonly (readonly [number, number])[] = [
    [0, 1], [2, 3], [4, 5], [6, 7],
    [0, 2], [1, 3], [4, 6], [5, 7],
    [0, 4], [1, 5], [2, 6], [3, 7],
];

/** The box's twelve edges as line segments (six numbers each). */
export function boxEdgeSegments(box: LocalBox, pose: BoxPose, out = new Float32Array(72)): Float32Array {
    const corners = boxCorners(box, pose);
    EDGES.forEach(([a, b], i) => {
        out.set(corners.subarray(a * 3, a * 3 + 3), i * 6);
        out.set(corners.subarray(b * 3, b * 3 + 3), i * 6 + 3);
    });
    return out;
}

/** The box's six faces as triangles over `boxCorners` (two each; drawn from both sides, so unordered). */
export const BOX_FACE_INDICES = new Uint32Array([
    0, 1, 3, 0, 3, 2, // z min
    4, 5, 7, 4, 7, 6, // z max
    0, 1, 5, 0, 5, 4, // y min
    2, 3, 7, 2, 7, 6, // y max
    0, 2, 6, 0, 6, 4, // x min
    1, 3, 7, 1, 7, 5, // x max
]);

/** How long the move arrows are and how wide the turn ring is: a box's half diagonal, so the handles
 *  stand clear of a box placed about its middle and stay in proportion to it. */
export function handleReach(box: LocalBox): number {
    return Math.max(0.5 * Math.hypot(box[3] - box[0], box[4] - box[1], box[5] - box[2]), 1e-3);
}

/** A move arrow: its shaft from `origin` along `axis` and the two barbs of its head (three segments). */
export function arrowSegments(origin: readonly number[], axis: GizmoAxis, length: number): Float32Array {
    const dir = AXES[axis];
    // The barbs lie in the floor's plane; an arrow along Z has them along X.
    const side = AXES[axis === 0 ? 1 : 0];
    const tip: Vec3 = [origin[0] + dir[0] * length, origin[1] + dir[1] * length, origin[2] + dir[2] * length];
    const head = length * 0.15;
    const barb = (sign: number): Vec3 => [
        tip[0] - dir[0] * head + side[0] * head * 0.5 * sign,
        tip[1] - dir[1] * head + side[1] * head * 0.5 * sign,
        tip[2] - dir[2] * head + side[2] * head * 0.5 * sign,
    ];
    return new Float32Array([origin[0], origin[1], origin[2], ...tip, ...tip, ...barb(1), ...tip, ...barb(-1)]);
}

/** The plane square's corners, in the floor's plane beside `origin`: grabbed, it moves the box freely in X and Y. */
export function planeSquareCorners(origin: readonly number[], length: number): Vec3[] {
    const a = length * 0.25, b = length * 0.5;
    return [[a, a], [b, a], [b, b], [a, b]].map(([x, y]) => [origin[0] + x, origin[1] + y, origin[2]] as Vec3);
}

/** A closed outline through `points` as line segments. */
export function loopSegments(points: readonly (readonly number[])[]): Float32Array {
    const out = new Float32Array(points.length * 6);
    points.forEach((p, i) => {
        const q = points[(i + 1) % points.length];
        out.set([p[0], p[1], p[2], q[0], q[1], q[2]], i * 6);
    });
    return out;
}

export const RING_SIDES = 48;

/** The turn ring's points: a circle about Z through `origin`, flat on the floor's plane. */
export function ringPoints(origin: readonly number[], radius: number, sides = RING_SIDES): Vec3[] {
    const out: Vec3[] = [];
    for (let i = 0; i < sides; i++) {
        const a = (i / sides) * 2 * Math.PI;
        out.push([origin[0] + radius * Math.cos(a), origin[1] + radius * Math.sin(a), origin[2]]);
    }
    return out;
}

/** The guide line of an axis-locked move: through `origin` along `axis`, far both ways (one segment). */
export function guideSegment(origin: readonly number[], axis: GizmoAxis, far = 1000): Float32Array {
    const dir = AXES[axis];
    return new Float32Array([
        origin[0] - dir[0] * far, origin[1] - dir[1] * far, origin[2] - dir[2] * far,
        origin[0] + dir[0] * far, origin[1] + dir[1] * far, origin[2] + dir[2] * far,
    ]);
}

// ----- the pointer on screen, as a move or a turn -------------------------------------------------

/** How far `p` is from the segment `a`-`b`, in pixels. */
export function distanceToSegment(p: ScreenPoint, a: ScreenPoint, b: ScreenPoint): number {
    const abx = b.x - a.x, aby = b.y - a.y;
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * abx + (p.y - a.y) * aby) / (abx * abx + aby * aby || 1)));
    return Math.hypot(p.x - (a.x + abx * t), p.y - (a.y + aby * t));
}

/** Whether `p` is inside the outline through `corners` (any polygon, either winding). */
export function insidePolygon(p: ScreenPoint, corners: readonly ScreenPoint[]): boolean {
    let inside = false;
    for (let i = 0, j = corners.length - 1; i < corners.length; j = i++) {
        const a = corners[i], b = corners[j];
        if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) inside = !inside;
    }
    return inside;
}

/** Where along an axis the pointer is, in the scene's units from `base`: for an axis drawn from `base`
 *  to `tip` on screen that is `length` long in the scene. Null for an axis seen end-on, which has no
 *  direction on screen to follow. */
export function alongAxis(p: ScreenPoint, base: ScreenPoint, tip: ScreenPoint, length: number): number | null {
    const ax = tip.x - base.x, ay = tip.y - base.y;
    const squared = ax * ax + ay * ay;
    return squared < 1 ? null : (((p.x - base.x) * ax + (p.y - base.y) * ay) / squared) * length;
}

/** Where in the floor's plane the pointer is, as (x, y) in the scene's units from the plane's origin:
 *  for an origin drawn at `o` and the points `length` along X and along Y from it drawn at `ex` and
 *  `ey`. Null for a plane seen edge-on. Exact for a view without perspective, and near the origin
 *  with it. */
export function inPlane(p: ScreenPoint, o: ScreenPoint, ex: ScreenPoint, ey: ScreenPoint, length: number): [number, number] | null {
    const a = ex.x - o.x, b = ey.x - o.x, c = ex.y - o.y, d = ey.y - o.y;
    const det = a * d - b * c;
    if (Math.abs(det) < 1) return null;
    const px = p.x - o.x, py = p.y - o.y;
    return [((d * px - b * py) / det) * length, ((a * py - c * px) / det) * length];
}

// ----- the box's faces, and the handles that move one ----------------------------------------------

/** A face of the box, by the axis of its own frame it faces along and which way: "+x" is the face at max x. */
export type BoxFace = "-x" | "+x" | "-y" | "+y" | "-z" | "+z";
export const BOX_FACES: readonly BoxFace[] = ["-x", "+x", "-y", "+y", "-z", "+z"];

/** The axis of the box's own frame a face is across. */
export function faceAxis(face: BoxFace): GizmoAxis {
    return face[1] === "x" ? 0 : face[1] === "y" ? 1 : 2;
}

/** 1 for a face at the max end of its axis, -1 for one at the min end. */
export function faceSign(face: BoxFace): 1 | -1 {
    return face[0] === "+" ? 1 : -1;
}

/** The middle of a face, in the scene, for where the box stands. */
export function faceCentre(box: LocalBox, pose: BoxPose, face: BoxFace): Vec3 {
    const axis = faceAxis(face);
    const local = [0, 1, 2].map((k) => (k === axis ? box[faceSign(face) > 0 ? k + 3 : k] : 0.5 * (box[k] + box[k + 3])));
    return poseToScene(pose, local);
}

/** A face's outward normal in the scene: its axis turned by the pose's turn about Z. */
export function faceNormal(pose: BoxPose, face: BoxFace): Vec3 {
    const axis = faceAxis(face), s = faceSign(face);
    if (pose.axes) return pose.axes[axis].map((v) => s * v) as Vec3;
    if (axis === 2) return [0, 0, s];
    const yaw = (pose.yawDeg * Math.PI) / 180;
    const c = Math.cos(yaw), n = Math.sin(yaw);
    return axis === 0 ? [s * c, s * n, 0] : [-s * n, s * c, 0];
}

/** An arrow from `origin` along the unit vector `dir`, `length` long, its barbs along `side` (three segments). */
export function arrowAlong(origin: readonly number[], dir: readonly number[], side: readonly number[], length: number): Float32Array {
    const tip: Vec3 = [origin[0] + dir[0] * length, origin[1] + dir[1] * length, origin[2] + dir[2] * length];
    const head = length * 0.3;
    const barb = (sign: number): Vec3 => [
        tip[0] - dir[0] * head + side[0] * head * 0.5 * sign,
        tip[1] - dir[1] * head + side[1] * head * 0.5 * sign,
        tip[2] - dir[2] * head + side[2] * head * 0.5 * sign,
    ];
    return new Float32Array([origin[0], origin[1], origin[2], ...tip, ...tip, ...barb(1), ...tip, ...barb(-1)]);
}

/** How long a face's resize handle is: a third of the move arrows. */
export function faceHandleLength(box: LocalBox): number {
    return handleReach(box) / 3;
}

/** A face's resize handle: an arrow out of its middle along its normal, its barbs in the face's
 *  plane (up and down for a side face, along the box's own x for its top and its floor). */
export function faceHandleSegments(box: LocalBox, pose: BoxPose, face: BoxFace): Float32Array {
    const normal = faceNormal(pose, face);
    const side: Vec3 = faceNormal(pose, faceAxis(face) === 2 ? "+x" : "+z");
    return arrowAlong(faceCentre(box, pose, face), normal, side, faceHandleLength(box));
}

/**
 * The box with `face` moved `outward` along its normal from where it was in `start` (inward for a
 * negative one), the opposite face staying where it is. The box's size across that face is what
 * snaps -- to multiples of `step`, as a move does -- and never goes under `minSize`.
 */
export function resizedBox(start: LocalBox, face: BoxFace, outward: number, step: number | null | undefined, minSize: number): LocalBox {
    const axis = faceAxis(face);
    const size = Math.max(snapTo(start[axis + 3] - start[axis] + outward, step), minSize);
    const out = [...start];
    if (faceSign(face) > 0) out[axis + 3] = Math.round((start[axis] + size) * 1e6) / 1e6;
    else out[axis] = Math.round((start[axis + 3] - size) * 1e6) / 1e6;
    return out as unknown as LocalBox;
}

// ----- a box turned every way ---------------------------------------------------------------------

/** The axes of a box turned `yawDeg` about Z only. */
export function axesOfYaw(yawDeg: number): BoxAxes {
    const a = (yawDeg * Math.PI) / 180;
    const c = Math.cos(a), s = Math.sin(a);
    return [[c, s, 0], [-s || 0, c, 0], [0, 0, 1]];
}

/** Where a box's own x heads seen from above, in degrees from +X (0 for an x along Z). */
export function yawOfAxes(axes: BoxAxes): number {
    const x = axes[0];
    return Math.hypot(x[0], x[1]) > 1e-9 ? Math.round(((Math.atan2(x[1], x[0]) * 180) / Math.PI) * 10) / 10 || 0 : 0;
}

/** Axes a plugin gives, made a box's: x and y as given made unit and square to each other (y kept as
 *  near as it can be), z their cross product. Null for numbers that are not two directions. */
export function orthonormalAxes(raw: readonly (readonly number[])[]): BoxAxes | null {
    if (raw.length < 2 || !raw.slice(0, 2).every((v) => v.length >= 3 && v.slice(0, 3).every((n) => Number.isFinite(n)))) return null;
    const unit = (v: readonly number[]): Vec3 | null => {
        const n = Math.hypot(v[0], v[1], v[2]);
        return n > 1e-9 ? [v[0] / n, v[1] / n, v[2] / n] : null;
    };
    const x = unit(raw[0]);
    if (!x) return null;
    const d = raw[1][0] * x[0] + raw[1][1] * x[1] + raw[1][2] * x[2];
    const y = unit([raw[1][0] - d * x[0], raw[1][1] - d * x[1], raw[1][2] - d * x[2]]);
    if (!y) return null;
    const z: Vec3 = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]];
    // No -0: a pose read back reads as it was given.
    return [x, y, z].map((a) => a.map((v) => v || 0)) as BoxAxes;
}

/** The two world axes a ring about `axis` lies along, in the turn's own order: a turn about it by a
 *  positive angle takes the first towards the second (Y to Z about X, Z to X about Y, X to Y about Z). */
export function ringPlane(axis: GizmoAxis): [Vec3, Vec3] {
    return axis === 0 ? [axisVector(1), axisVector(2)] : axis === 1 ? [axisVector(2), axisVector(0)] : [axisVector(0), axisVector(1)];
}

/** A turn ring's points about a world axis: a circle through `origin`, square to `axis`. */
export function ringPointsAbout(origin: readonly number[], axis: GizmoAxis, radius: number, sides = RING_SIDES): Vec3[] {
    const [u, v] = ringPlane(axis);
    const out: Vec3[] = [];
    for (let i = 0; i < sides; i++) {
        const a = (i / sides) * 2 * Math.PI;
        const c = radius * Math.cos(a), s = radius * Math.sin(a);
        out.push([origin[0] + u[0] * c + v[0] * s, origin[1] + u[1] * c + v[1] * s, origin[2] + u[2] * c + v[2] * s]);
    }
    return out;
}

/** A vector turned `deg` about a world axis (anticlockwise, looking down the axis at the origin). */
export function turnAbout(v: readonly number[], axis: GizmoAxis, deg: number): Vec3 {
    const a = (deg * Math.PI) / 180;
    const c = Math.cos(a), s = Math.sin(a);
    const [i, j] = axis === 0 ? [1, 2] : axis === 1 ? [2, 0] : [0, 1];
    const out = [v[0], v[1], v[2]] as Vec3;
    out[i] = c * v[i] - s * v[j];
    out[j] = s * v[i] + c * v[j];
    return out;
}

/** A box's axes turned `deg` about a world axis, rounded to the micro-unit so a quarter turn reads 0 and 1. */
export function turnedAxes(axes: BoxAxes, axis: GizmoAxis, deg: number): BoxAxes {
    const round = (v: number) => Math.round(v * 1e9) / 1e9 || 0;
    return axes.map((a) => turnAbout(a, axis, deg).map(round)) as BoxAxes;
}

/** `value` on the nearest multiple of `step`; as it is for no step. */
export function snapTo(value: number, step: number | null | undefined): number {
    if (!step || step <= 0) return value;
    // Through a rounding to the micro-unit, so 0.1 steps read 0.3 and not 0.30000000000000004.
    return Math.round(Math.round(value / step) * step * 1e6) / 1e6 || 0;
}

/** A turn of `fromDeg` to `toDeg` as the shortest way round, in (-180, 180]. */
export function turnBetween(fromDeg: number, toDeg: number): number {
    const d = (((toDeg - fromDeg) % 360) + 360) % 360;
    return d > 180 ? d - 360 : d;
}
