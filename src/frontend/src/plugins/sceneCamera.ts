// A camera a plugin places in the scene (`ctx.scene.placeCamera`, plugin API 1.32.0).
//
// A tool that asks the user where a view is seen from -- "draw the frame from up here, looking at its
// middle" -- needs a thing to look at that says which way it looks, and handles to move and aim it.
// This is that, as `placeBox` (./sceneBox.ts) is for where something stands: core draws a camera
// (a body, a lens, and an arrow from it to what it looks at) with a transform gizmo on it, and tells
// the plugin the camera's pose -- where it is and what it looks at -- each time it changes. The plugin
// says where it is when its own numbers change (`set`), so a form and the camera stay in step.
//
// THE GIZMO answers the same keys as a placed box's, by the cell builder's rules
// (utils/gizmo/transformKeys.ts): G the move arrows, R the turn rings, X / Y / Z lock the move to
// that axis (following the pointer until a click, Escape puts it back), Escape and Enter end the
// placement. A move takes the camera, and it goes on looking at its target: moving it is aiming it
// from somewhere else. A turn is the camera's own, about where it stands: the flat ring turns it
// about Z (yaw), the standing ring tilts it up or down (pitch, kept off straight up and down), and
// the target goes round with it, as far away as it was. In the turn gizmo Z keeps the flat ring
// only. A drag snaps as a box's: a move to the step, a turn to 15 degrees.
//
// As the box's, the handles are lines drawn through the viewport interface and dragged by where the
// viewport says they are on screen, so the same code runs on both viewports. No three.js in this
// module or in anything it imports.

import {HANDLE_AXIS_COLOR} from "@/components/viewer/cellbuilder/sceneConstants";
import {
    BOX_FACE_INDICES,
    alongAxis,
    arrowSegments,
    axisVector,
    distanceToSegment,
    guideSegment,
    inPlane,
    insidePolygon,
    loopSegments,
    planeSquareCorners,
    ringPoints,
    snapTo,
    turnBetween,
    type ScreenPoint,
    type Vec3,
} from "@/utils/gizmo/boxPose";
import {
    MAX_PITCH_DEG,
    aheadOf,
    cameraArrowSegments,
    cameraBodyCorners,
    cameraBodySegments,
    cameraDistance,
    lookDirection,
    pitchRingPoints,
    turnedCamera,
    yawPitchOf,
    type CameraPose,
} from "@/utils/gizmo/cameraPose";
import {
    GIZMO_FALLBACK_STEP,
    GIZMO_ROTATE_SNAP_DEG,
    gizmoAxisOfKey,
    gizmoModeOfKey,
    isTextEntry,
    toggledAxisLock,
    type GizmoAxis,
} from "@/utils/gizmo/transformKeys";
import {linearColor, linearHex} from "@/utils/scene/sceneColor";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";
import {followActiveViewport} from "@/viewport/api/viewportApi";
import {GRAB_PX, pageEnv, type BoxPlacementEnv, type KeyLike, type Listener, type PointerLike} from "./sceneBox";
import {own} from "./sceneLayers";

/** Where the camera is and what it looks at, in the scene's own coordinates (those `placeBox` takes:
 *  the loaded model's file's, z-up). */
export interface PlacedCameraPose {
    position: [number, number, number];
    target: [number, number, number];
}

export type PlacedCameraMode = "translate" | "rotate";

export interface PlaceCameraOptions {
    position: readonly number[];
    /** What the camera looks at. */
    target: readonly number[];
    /** How long the camera's body is drawn, in the scene's units: a tenth of its distance to the
     *  target when left out. The handles are in proportion to it. */
    size?: number;
    /** What a drag snaps to: a move to multiples of `translate` (0.1 when left out; 0 for none), a turn
     *  to steps of `rotateDeg` (15; 0 for none). */
    snap?: {translate?: number; rotateDeg?: number};
    /** The gizmo shown first: "translate" when left out. */
    mode?: PlacedCameraMode;
    /** The camera's colour, as CSS. */
    color?: string;
    /** Frame the view on the camera and its target when first drawn. Once. */
    frame?: boolean;
    /** The user moved or turned the camera: where it is and what it looks at now. Not called for the plugin's own `set`. */
    onChange: (pose: PlacedCameraPose) => void;
    /** The user changed the gizmo (G and R), or the plugin did (`setMode`). */
    onMode?: (mode: PlacedCameraMode) => void;
    /** The user ended the placement with Enter or Escape: the camera is gone from the scene. Not called for the plugin's own `end`. */
    onEnd?: (how: "enter" | "escape") => void;
}

/** A camera being placed. */
export interface PlacedCamera {
    /** Put the camera somewhere else, aim it at something else, or draw it another size: what is left out stays. Tells nobody. */
    set: (change: {position?: readonly number[]; target?: readonly number[]; size?: number}) => void;
    pose: () => PlacedCameraPose;
    /** Show the move arrows or the turn rings (what G and R do). */
    setMode: (mode: PlacedCameraMode) => void;
    /** Take the camera and its gizmo out of the scene and give the pointer and the keys back. */
    end: () => void;
}

type Grab = {kind: "axis"; axis: GizmoAxis} | {kind: "plane"} | {kind: "yaw"} | {kind: "pitch"};

interface Drag {
    pointerId: number;
    grab: Grab;
    start: CameraPose;
    o: ScreenPoint;
    tip: ScreenPoint;
    ex: ScreenPoint;
    ey: ScreenPoint;
    t0: number;
    q0: [number, number];
    bearing: number;
    turned: number;
    yawDeg: number;
    pitchDeg: number;
}

interface ModalMove {
    axis: GizmoAxis;
    start: CameraPose;
    startT: number | null;
}

const CAMERA_COLOR = "#f472b6";
const FACE_OPACITY = 0.14;
const copy = (pose: CameraPose): CameraPose => ({position: [...pose.position] as Vec3, target: [...pose.target] as Vec3});
const vec3 = (v: readonly number[], what: string): Vec3 => {
    if (v.length < 3 || !v.slice(0, 3).every((n) => Number.isFinite(n))) throw new Error(`placeCamera: ${what} needs three finite numbers`);
    return [v[0], v[1], v[2]];
};
const bearingOf = (q: readonly number[]): number => (Math.atan2(q[1], q[0]) * 180) / Math.PI;
const tenth = (v: number): number => Math.round(v * 10) / 10;

/**
 * One camera and its gizmo: where it is and what it looks at, which handles are up, and the drag or
 * the axis-locked move in progress. Drawn in, and taking the input of, whatever it is attached to
 * (`attach`), as a placed box is.
 */
export class CameraPlacement {
    private at: CameraPose;
    private size: number;
    private mode: PlacedCameraMode;
    private lock: GizmoAxis | null = null;
    private readonly step: number;
    private readonly turnStep: number;
    private env: BoxPlacementEnv | null = null;
    private layer: OverlayLayer | null = null;
    private faces = 0;
    private body = 0;
    private arrow = 0;
    private handles: {handle: number; segments: () => Float32Array}[] = [];
    private drag: Drag | null = null;
    private modal: ModalMove | null = null;
    private swallowClick = false;
    private hovering = false;
    private framed = false;
    private detachEnv: (() => void) | null = null;
    onUserEnd: (() => void) | null = null;

    constructor(
        private readonly options: PlaceCameraOptions,
        private readonly layerName: string,
    ) {
        this.at = {position: vec3(options.position, "position"), target: vec3(options.target, "target")};
        this.size = this.sizeOf(options.size);
        this.step = options.snap?.translate ?? GIZMO_FALLBACK_STEP;
        this.turnStep = options.snap?.rotateDeg ?? GIZMO_ROTATE_SNAP_DEG;
        this.mode = options.mode === "rotate" ? "rotate" : "translate";
    }

    private sizeOf(size: number | undefined): number {
        if (size !== undefined && !(Number.isFinite(size) && size > 0)) throw new Error("placeCamera: size needs a number more than 0");
        return size ?? Math.max(0.1 * cameraDistance(this.at), 1e-3);
    }

    pose(): PlacedCameraPose {
        return copy(this.at);
    }

    gizmo(): {mode: PlacedCameraMode; axisLock: GizmoAxis | null; following: boolean; dragging: boolean} {
        return {mode: this.mode, axisLock: this.lock, following: this.modal !== null, dragging: this.drag !== null};
    }

    set(change: {position?: readonly number[]; target?: readonly number[]; size?: number}): void {
        const next: CameraPose = {
            position: change.position ? vec3(change.position, "position") : this.at.position,
            target: change.target ? vec3(change.target, "target") : this.at.target,
        };
        if (change.size !== undefined) this.size = this.sizeOf(change.size);
        this.at = next;
        this.move();
    }

    setMode(mode: PlacedCameraMode): void {
        if (mode !== "translate" && mode !== "rotate") return;
        if (mode === this.mode && this.lock === null) return;
        const changed = mode !== this.mode;
        this.mode = mode;
        this.lock = null;
        this.endModal(false);
        this.draw();
        if (changed) this.options.onMode?.(mode);
    }

    // ----- attached to a viewport -------------------------------------------------------------------

    attach(env: BoxPlacementEnv): void {
        this.detach();
        this.env = env;
        this.layer = env.viewport.overlays(this.layerName);
        const input = env.input;
        const offs: (() => void)[] = [env.onOffsetChange(() => this.draw())];
        if (input) {
            const pointer: [string, Listener<PointerLike>][] = [
                ["pointerdown", (e) => this.onPointerDown(e)],
                ["pointermove", (e) => this.onPointerMove(e)],
                ["pointerup", (e) => this.onPointerUp(e)],
                ["pointercancel", (e) => this.onPointerUp(e)],
                ["click", (e) => this.onClick(e)],
            ];
            for (const [type, listener] of pointer) {
                input.addEventListener(type, listener, true);
                offs.push(() => input.removeEventListener(type, listener, true));
            }
            if (env.keys) {
                const keys = env.keys;
                const onKey: Listener<KeyLike> = (e) => this.onKeyDown(e);
                keys.addEventListener("keydown", onKey, true);
                offs.push(() => keys.removeEventListener("keydown", onKey, true));
            }
        }
        this.detachEnv = () => offs.forEach((off) => off());
        this.draw();
        if (this.options.frame && !this.framed) this.framed = this.frame(env);
    }

    private frame(env: BoxPlacementEnv): boolean {
        if (!env.viewport.fitBox) return false;
        const offset = env.offset() ?? [0, 0, 0];
        const pad = this.size;
        const min = [0, 1, 2].map((k) => Math.min(this.at.position[k], this.at.target[k]) - pad + offset[k]);
        const max = [0, 1, 2].map((k) => Math.max(this.at.position[k], this.at.target[k]) + pad + offset[k]);
        return env.viewport.fitBox(min as Vec3, max as Vec3);
    }

    detach(): void {
        if (!this.env) return;
        if (this.drag) this.env.input?.releasePointerCapture?.(this.drag.pointerId);
        this.drag = null;
        this.endModal(false);
        this.lock = null;
        this.swallowClick = false;
        if (this.hovering && this.env.input?.style) this.env.input.style.cursor = "";
        this.hovering = false;
        this.detachEnv?.();
        this.detachEnv = null;
        this.layer?.dispose();
        this.layer = null;
        this.handles = [];
        this.env = null;
    }

    // ----- drawing ----------------------------------------------------------------------------------

    /** How long the move arrows are and how wide the rings: one and a half bodies. */
    private reach(): number {
        return 1.5 * this.size;
    }

    private handleSpecs(): {color: readonly [number, number, number]; widthPx: number; opacity?: number; segments: () => Float32Array}[] {
        const colour = (axis: GizmoAxis) => linearHex(HANDLE_AXIS_COLOR[axis]);
        const origin = () => this.at.position;
        if (this.modal) {
            const axis = this.modal.axis;
            return [{color: colour(axis), widthPx: 1, opacity: 0.6, segments: () => guideSegment(origin(), axis)}];
        }
        if (this.mode === "rotate") {
            const yaw = {color: colour(2), widthPx: 3, segments: () => loopSegments(ringPoints(origin(), this.reach()))};
            const pitch = {color: colour(0), widthPx: 3, segments: () => loopSegments(pitchRingPoints(this.at, this.reach()))};
            return this.lock === 2 ? [yaw] : [yaw, pitch];
        }
        return [
            ...([0, 1, 2] as GizmoAxis[]).map((axis) => ({color: colour(axis), widthPx: 3, segments: () => arrowSegments(origin(), axis, this.reach())})),
            {color: colour(2), widthPx: 2, segments: () => loopSegments(planeSquareCorners(origin(), this.reach()))},
        ];
    }

    private draw(): void {
        const layer = this.layer, env = this.env;
        if (!layer || !env) return;
        layer.clear();
        const offset = env.offset();
        const matrix = offset && (offset[0] || offset[1] || offset[2]) ? [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, offset[0], offset[1], offset[2], 1] : null;
        const color = linearColor(this.options.color ?? CAMERA_COLOR);
        // A marker in the scene, not a part of the model: never what the camera frames, never cut by a section.
        const look = {matrix, fitted: false, clipped: false};
        this.faces = layer.addMesh({positions: cameraBodyCorners(this.at, this.size), indices: BOX_FACE_INDICES, color, opacity: FACE_OPACITY, ...look});
        this.body = layer.addLines({segments: cameraBodySegments(this.at, this.size), color, widthPx: 2, onTop: true, ...look});
        this.arrow = layer.addLines({segments: cameraArrowSegments(this.at, this.size), color, widthPx: 2, onTop: true, ...look});
        this.handles = this.handleSpecs().map((spec) => ({
            handle: layer.addLines({segments: spec.segments(), color: spec.color, widthPx: spec.widthPx, opacity: spec.opacity, onTop: true, ...look}),
            segments: spec.segments,
        }));
    }

    private move(): void {
        const layer = this.layer;
        if (!layer) return;
        layer.setPositions(this.faces, cameraBodyCorners(this.at, this.size));
        layer.setPositions(this.body, cameraBodySegments(this.at, this.size));
        layer.setPositions(this.arrow, cameraArrowSegments(this.at, this.size));
        for (const {handle, segments} of this.handles) layer.setPositions(handle, segments());
    }

    // ----- where things are on screen -----------------------------------------------------------------

    private screen(points: readonly (readonly number[])[]): (ScreenPoint | null)[] {
        const env = this.env;
        if (!env) return points.map(() => null);
        const offset = env.offset() ?? [0, 0, 0];
        const world = new Float32Array(points.length * 3);
        points.forEach((p, i) => world.set([p[0] + offset[0], p[1] + offset[1], p[2] + offset[2]], i * 3));
        const out = new Float32Array(points.length * 3);
        env.viewport.projectToScreen(world, points.length, out);
        return points.map((_, i) => (Number.isFinite(out[i * 3]) && Number.isFinite(out[i * 3 + 1]) ? {x: out[i * 3], y: out[i * 3 + 1]} : null));
    }

    private pointer(e: PointerLike): ScreenPoint {
        const rect = this.env?.input?.getBoundingClientRect() ?? {left: 0, top: 0};
        return {x: e.clientX - rect.left, y: e.clientY - rect.top};
    }

    private along(origin: readonly number[], dir: readonly number[]): Vec3 {
        const reach = this.reach();
        return [origin[0] + dir[0] * reach, origin[1] + dir[1] * reach, origin[2] + dir[2] * reach];
    }

    private nearRing(p: ScreenPoint, points: readonly Vec3[]): number {
        const ring = this.screen(points);
        let best = Infinity;
        ring.forEach((a, i) => {
            const b = ring[(i + 1) % ring.length];
            if (a && b) best = Math.min(best, distanceToSegment(p, a, b));
        });
        return best;
    }

    private hit(p: ScreenPoint): Grab | null {
        if (this.modal) return null;
        const origin = this.at.position;
        if (this.mode === "rotate") {
            const yaw = this.nearRing(p, ringPoints(origin, this.reach()));
            const pitch = this.lock === 2 ? Infinity : this.nearRing(p, pitchRingPoints(this.at, this.reach()));
            if (Math.min(yaw, pitch) > GRAB_PX) return null;
            return pitch < yaw ? {kind: "pitch"} : {kind: "yaw"};
        }
        const square = planeSquareCorners(origin, this.reach());
        const [o, ...rest] = this.screen([origin, ...[0, 1, 2].map((axis) => this.along(origin, axisVector(axis as GizmoAxis))), ...square]);
        if (!o) return null;
        let nearest: GizmoAxis | null = null;
        let within = GRAB_PX;
        for (let i = 0; i < 3; i++) {
            const tip = rest[i];
            const distance = tip ? distanceToSegment(p, o, tip) : Infinity;
            if (distance <= within) {
                within = distance;
                nearest = i as GizmoAxis;
            }
        }
        if (nearest !== null) return {kind: "axis", axis: nearest};
        const corners = rest.slice(3);
        if (corners.every((c) => c !== null) && insidePolygon(p, corners as ScreenPoint[])) return {kind: "plane"};
        return null;
    }

    // ----- the pointer --------------------------------------------------------------------------------

    private take(e: PointerLike): void {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
    }

    private onPointerDown(e: PointerLike): void {
        this.swallowClick = false;
        if (e.button !== 0 || this.drag) return;
        if (this.modal) {
            this.endModal(false);
            this.lock = null;
            this.draw();
            this.swallowClick = true;
            return this.take(e);
        }
        const p = this.pointer(e);
        const grab = this.hit(p);
        if (!grab) return;
        const origin = this.at.position;
        // The plane the pointer is followed in: the floor's (X, Y) for a move and the flat ring, the
        // look direction's vertical plane (ahead, Z) for the standing ring.
        const [ux, uy]: [Vec3, Vec3] = grab.kind === "pitch" ? [aheadOf(this.at), [0, 0, 1]] : [[1, 0, 0], [0, 1, 0]];
        const tipAxis: Vec3 = grab.kind === "axis" ? axisVector(grab.axis) : [0, 0, 1];
        const [o, ex, ey, tip] = this.screen([origin, this.along(origin, ux), this.along(origin, uy), this.along(origin, tipAxis)]);
        if (!o || !ex || !ey || !tip) return;
        const q0 = inPlane(p, o, ex, ey, this.reach()) ?? [0, 0];
        const {yawDeg, pitchDeg} = yawPitchOf(lookDirection(this.at));
        this.drag = {
            pointerId: e.pointerId, grab, start: copy(this.at), o, tip, ex, ey,
            t0: alongAxis(p, o, tip, this.reach()) ?? 0, q0, bearing: bearingOf(q0), turned: 0, yawDeg, pitchDeg,
        };
        this.env?.input?.setPointerCapture?.(e.pointerId);
        this.swallowClick = true;
        this.take(e);
    }

    private onPointerMove(e: PointerLike): void {
        const p = this.pointer(e);
        const drag = this.drag;
        if (drag) {
            if (e.pointerId !== drag.pointerId) return;
            this.take(e);
            const reach = this.reach();
            if (drag.grab.kind === "axis") {
                const t = alongAxis(p, drag.o, drag.tip, reach);
                if (t === null) return;
                const next = copy(drag.start);
                next.position[drag.grab.axis] = snapTo(drag.start.position[drag.grab.axis] + t - drag.t0, this.step);
                return this.userMoved(next);
            }
            const q = inPlane(p, drag.o, drag.ex, drag.ey, reach);
            if (!q) return;
            if (drag.grab.kind === "plane") {
                const next = copy(drag.start);
                next.position[0] = snapTo(drag.start.position[0] + q[0] - drag.q0[0], this.step);
                next.position[1] = snapTo(drag.start.position[1] + q[1] - drag.q0[1], this.step);
                return this.userMoved(next);
            }
            const bearing = bearingOf(q);
            drag.turned += turnBetween(drag.bearing, bearing);
            drag.bearing = bearing;
            const by = snapTo(drag.turned, this.turnStep);
            if (drag.grab.kind === "yaw") return this.userMoved(turnedCamera(drag.start, tenth(drag.yawDeg + by), drag.pitchDeg));
            const pitch = Math.max(-MAX_PITCH_DEG, Math.min(MAX_PITCH_DEG, tenth(drag.pitchDeg + by)));
            return this.userMoved(turnedCamera(drag.start, drag.yawDeg, pitch));
        }
        const modal = this.modal;
        if (modal) {
            const [o, tip] = this.screen([modal.start.position, this.along(modal.start.position, axisVector(modal.axis))]);
            const t = o && tip ? alongAxis(p, o, tip, this.reach()) : null;
            if (t === null) return;
            if (modal.startT === null) return void (modal.startT = t);
            const next = copy(modal.start);
            next.position[modal.axis] = snapTo(modal.start.position[modal.axis] + t - modal.startT, this.step);
            return this.userMoved(next);
        }
        const over = this.hit(p) !== null;
        const style = this.env?.input?.style;
        if (style && over !== this.hovering) style.cursor = over ? "grab" : "";
        this.hovering = over;
    }

    private onPointerUp(e: PointerLike): void {
        const drag = this.drag;
        if (!drag || e.pointerId !== drag.pointerId) return;
        this.drag = null;
        this.env?.input?.releasePointerCapture?.(e.pointerId);
        this.take(e);
    }

    private onClick(e: PointerLike): void {
        if (!this.swallowClick) return;
        this.swallowClick = false;
        this.take(e);
    }

    private userMoved(next: CameraPose): void {
        const same = next.position.every((v, i) => v === this.at.position[i]) && next.target.every((v, i) => v === this.at.target[i]);
        if (same) return;
        this.at = next;
        this.move();
        this.options.onChange(copy(next));
    }

    // ----- the axis-locked move, and the keys ---------------------------------------------------------

    private startModal(axis: GizmoAxis): void {
        this.modal = {axis, start: copy(this.at), startT: null};
        const style = this.env?.input?.style;
        if (style) style.cursor = "move";
    }

    private endModal(cancel: boolean): void {
        const modal = this.modal;
        if (!modal) return;
        this.modal = null;
        const style = this.env?.input?.style;
        if (style) style.cursor = "";
        if (cancel && modal.startT !== null) this.userMoved(modal.start);
    }

    private onKeyDown(e: KeyLike): void {
        if (isTextEntry(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
        const consume = () => {
            e.preventDefault();
            e.stopPropagation();
        };
        if (e.key === "Escape" || e.key === "Enter") {
            consume();
            if (this.modal) {
                this.endModal(e.key === "Escape");
                this.lock = null;
                return this.draw();
            }
            const how = e.key === "Enter" ? "enter" : "escape";
            this.onUserEnd?.();
            return void this.options.onEnd?.(how);
        }
        if (e.shiftKey) return;
        const key = e.key.toLowerCase();
        const mode = gizmoModeOfKey(key);
        // S (resize) is not a camera's: left to whatever else answers it.
        if (mode === "translate" || mode === "rotate") {
            consume();
            return this.setMode(mode);
        }
        const axis = gizmoAxisOfKey(key);
        if (axis === null) return;
        consume();
        // The turn gizmo's one axis is Z, its flat ring: the standing ring turns about no axis of the scene.
        if (this.mode === "rotate" && axis !== 2) return;
        this.endModal(false);
        this.lock = toggledAxisLock(this.lock, axis);
        if (this.mode === "translate" && this.lock !== null) this.startModal(this.lock);
        this.draw();
    }
}

// ----- `ctx.scene.placeCamera` ----------------------------------------------------------------------

let placements = 0;

/** Place a camera in the scene until `end()` (or until `owner` is removed from the scene): drawn by
 *  the viewport on screen, and by the one that replaces it. */
export function placeCamera(owner: string, options: PlaceCameraOptions): PlacedCamera {
    const placement = new CameraPlacement(options, `plugin:${owner}:camera:${++placements}`);
    const unfollow = followActiveViewport((viewport) => {
        placement.attach(pageEnv(viewport));
        return () => placement.detach();
    });
    const end = own(owner, () => {
        unfollow();
        placement.detach();
    });
    placement.onUserEnd = end;
    return {
        set: (change) => placement.set(change),
        pose: () => placement.pose(),
        setMode: (mode) => placement.setMode(mode),
        end,
    };
}
