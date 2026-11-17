// A box a plugin places in the scene (`ctx.scene.placeBox`, plugin API 1.30.0).
//
// A tool that asks the user where something stands -- "put the machine's envelope here, turned so" --
// needs more than a click: a thing to look at, and handles to move and turn it by. This is that: core
// draws a simple box (its edges, and its faces faintly) with a transform gizmo at its origin, and tells
// the plugin the box's pose each time it changes. The plugin says where the box is when its own numbers
// change (`set`), so a form and the box stay in step both ways.
//
// THE GIZMO. It answers the cell builder's keys, by the cell builder's own rules
// (utils/gizmo/transformKeys.ts, which its key handler reads too): G brings up the move arrows, R the
// turn ring, X / Y / Z lock the gizmo to that axis (the same key again releases it), and a move locked
// to an axis follows the pointer with no button held until a click confirms it or Escape puts the box
// back. A drag snaps as the builder's does: a move to the grid step, a turn to 15 degrees. Escape and
// Enter end the placement where the box stands. What is NOT the builder's own is the widget: the
// builder's is three.js's TransformControls, an object of a three.js scene, and the three.js-free
// viewport has no such scene. The handles here are lines drawn through the viewport interface
// (viewport/api/overlayLayer.ts) in the builder's axis colours and dragged by where the viewport says
// they are on screen (utils/gizmo/boxPose.ts), so the same code runs on both viewports.
//
// The pointer is taken only for a press on a handle (and for the click that confirms an axis-locked
// move): any other press is the camera's, and a click selects as always. The keys are the gizmo's while
// the box is placed, except in a form field, and are given back when it ends.
//
// No three.js in this module or in anything it imports.

import {HANDLE_AXIS_COLOR} from "@/components/viewer/cellbuilder/sceneConstants";
import {useSceneFrameStore} from "@/state/sceneFrameStore";
import {
    BOX_FACE_INDICES,
    alongAxis,
    arrowSegments,
    axisVector,
    boxCorners,
    boxEdgeSegments,
    distanceToSegment,
    guideSegment,
    handleReach,
    inPlane,
    insidePolygon,
    loopSegments,
    planeSquareCorners,
    ringPoints,
    snapTo,
    turnBetween,
    type BoxPose,
    type LocalBox,
    type ScreenPoint,
    type Vec3,
} from "@/utils/gizmo/boxPose";
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
import {VIEWPORT_INPUT_ATTR} from "@/viewport/input";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";
import {followActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";
import {own} from "./sceneLayers";

/** Where the box stands: its origin in the scene's own coordinates (those of the loaded model's file,
 *  z-up: the frame `ScenePick.point` and `addLineLayer` use) and its turn about Z in degrees,
 *  anticlockwise seen from above. */
export interface PlacedBoxPose {
    position: [number, number, number];
    yawDeg: number;
}

export type PlacedBoxMode = "translate" | "rotate";

export interface PlaceBoxOptions {
    /** The box in its own frame: min x, y, z, then max x, y, z. Its origin (0, 0, 0) is the point the
     *  pose places, the gizmo stands at and a turn goes about; it need not be inside the box. */
    box: readonly number[];
    position: readonly number[];
    /** 0 when left out. */
    yawDeg?: number;
    /** What the gizmo may do. `translate`: "xyz" (the default) or "xy" for a box that stays on its
     *  floor; `rotate`: "z" (the default) or "none". */
    constrain?: {translate?: "xyz" | "xy"; rotate?: "z" | "none"};
    /** What a drag snaps to: a move to multiples of `translate` (0.1 when left out, the cell builder's
     *  step; 0 for none) along the dragged axes, a turn to steps of `rotateDeg` (15; 0 for none). An
     *  exact value is the plugin's to `set`. */
    snap?: {translate?: number; rotateDeg?: number};
    /** The gizmo shown first: "translate" when left out. */
    mode?: PlacedBoxMode;
    /** The box's colour, as CSS. */
    color?: string;
    /** The user moved or turned the box: where it stands now. Not called for the plugin's own `set`. */
    onChange: (pose: PlacedBoxPose) => void;
    /** The user changed the gizmo (the G and R keys), or the plugin did (`setMode`). */
    onMode?: (mode: PlacedBoxMode) => void;
    /** The user ended the placement with Enter or Escape: the box is gone from the scene, and stood
     *  where the last `onChange` said. Not called for the plugin's own `end`. */
    onEnd?: (how: "enter" | "escape") => void;
}

/** A box being placed. */
export interface PlacedBox {
    /** Put the box somewhere else, or make it another box: what is left out stays. Tells nobody. */
    set: (change: {position?: readonly number[]; yawDeg?: number; box?: readonly number[]}) => void;
    pose: () => PlacedBoxPose;
    /** Show the move arrows or the turn ring (what G and R do). */
    setMode: (mode: PlacedBoxMode) => void;
    /** Take the box and its gizmo out of the scene and give the pointer and the keys back. */
    end: () => void;
}

// ----- what the placement asks of the page: a viewport, its input surface, the keys ----------------

interface PointerLike {
    clientX: number;
    clientY: number;
    button: number;
    pointerId: number;
    preventDefault(): void;
    stopPropagation(): void;
    stopImmediatePropagation(): void;
}

interface KeyLike {
    key: string;
    target: unknown;
    ctrlKey: boolean;
    metaKey: boolean;
    altKey: boolean;
    shiftKey: boolean;
    preventDefault(): void;
    stopPropagation(): void;
}

type Listener<E> = (event: E) => void;

/** The element the view's pointer events arrive at (viewport/input.ts). */
export interface BoxInput {
    addEventListener(type: string, listener: Listener<PointerLike>, capture: boolean): void;
    removeEventListener(type: string, listener: Listener<PointerLike>, capture: boolean): void;
    getBoundingClientRect(): {left: number; top: number};
    setPointerCapture?(pointerId: number): void;
    releasePointerCapture?(pointerId: number): void;
    style?: {cursor: string};
}

export interface BoxKeys {
    addEventListener(type: "keydown", listener: Listener<KeyLike>, capture: boolean): void;
    removeEventListener(type: "keydown", listener: Listener<KeyLike>, capture: boolean): void;
}

export interface BoxPlacementEnv {
    viewport: Pick<ViewportApi, "overlays" | "projectToScreen">;
    /** Null where the view has no input surface: the box is drawn and follows `set`, and is not dragged. */
    input: BoxInput | null;
    keys: BoxKeys | null;
    /** world = scene + offset (state/sceneFrameStore); null for none. */
    offset(): readonly number[] | null;
    onOffsetChange(listener: () => void): () => void;
}

/** A press this close to a handle, in pixels, takes it. */
export const GRAB_PX = 14;

type Grab = {kind: "axis"; axis: GizmoAxis} | {kind: "plane"} | {kind: "turn"};

interface Drag {
    pointerId: number;
    grab: Grab;
    start: BoxPose;
    /** The handle's frame on screen when it was grabbed: the origin, and the points one reach along X, Y (and the grabbed axis). */
    o: ScreenPoint;
    tip: ScreenPoint;
    ex: ScreenPoint;
    ey: ScreenPoint;
    /** Where the press was: along the axis, or in the plane. */
    t0: number;
    q0: [number, number];
    /** The pointer's bearing in the plane at the last move, and how far it has gone round since the press. */
    bearing: number;
    turned: number;
}

interface ModalMove {
    axis: GizmoAxis;
    start: BoxPose;
    /** Where along the axis the pointer was at its first move; null until then, so the box does not jump. */
    startT: number | null;
}

const BOX_COLOR = "#38bdf8";
const FACE_OPACITY = 0.14;
const copy = (pose: BoxPose): BoxPose => ({position: [...pose.position] as Vec3, yawDeg: pose.yawDeg});
const vec3 = (v: readonly number[], what: string): Vec3 => {
    if (v.length < 3 || !v.slice(0, 3).every((n) => Number.isFinite(n))) throw new Error(`placeBox: ${what} needs three finite numbers`);
    return [v[0], v[1], v[2]];
};
const localBox = (v: readonly number[]): LocalBox => {
    if (v.length !== 6 || !v.every((n) => Number.isFinite(n)) || v[3] < v[0] || v[4] < v[1] || v[5] < v[2]) {
        throw new Error("placeBox: box needs six finite numbers, min x, y, z then max x, y, z");
    }
    return [v[0], v[1], v[2], v[3], v[4], v[5]];
};
const bearingOf = (q: readonly number[]): number => (Math.atan2(q[1], q[0]) * 180) / Math.PI;

/**
 * One box and its gizmo: where it stands, which handles are up, and the drag or the axis-locked move in
 * progress. It is drawn in, and takes the input of, whatever it is attached to (`attach`), and keeps
 * its state when that changes: the viewport on screen may be replaced while a box is placed.
 */
export class BoxPlacement {
    private box: LocalBox;
    private at: BoxPose;
    private mode: PlacedBoxMode;
    private lock: GizmoAxis | null = null;
    private readonly moveAxes: readonly GizmoAxis[];
    private readonly canTurn: boolean;
    private readonly step: number;
    private readonly turnStep: number;
    private env: BoxPlacementEnv | null = null;
    private layer: OverlayLayer | null = null;
    private faces = 0;
    private edges = 0;
    private handles: {handle: number; segments: () => Float32Array}[] = [];
    private drag: Drag | null = null;
    private modal: ModalMove | null = null;
    /** A handle's press ends in a click the scene must not also select by. */
    private swallowClick = false;
    private hovering = false;
    private detachEnv: (() => void) | null = null;
    /** Told when the user ended the placement by key, before `onEnd`. */
    onUserEnd: (() => void) | null = null;

    constructor(
        private readonly options: PlaceBoxOptions,
        private readonly layerName: string,
    ) {
        this.box = localBox(options.box);
        this.at = {position: vec3(options.position, "position"), yawDeg: options.yawDeg ?? 0};
        this.moveAxes = options.constrain?.translate === "xy" ? [0, 1] : [0, 1, 2];
        this.canTurn = options.constrain?.rotate !== "none";
        this.step = options.snap?.translate ?? GIZMO_FALLBACK_STEP;
        this.turnStep = options.snap?.rotateDeg ?? GIZMO_ROTATE_SNAP_DEG;
        this.mode = options.mode === "rotate" && this.canTurn ? "rotate" : "translate";
    }

    pose(): PlacedBoxPose {
        return copy(this.at);
    }

    gizmo(): {mode: PlacedBoxMode; axisLock: GizmoAxis | null; following: boolean; dragging: boolean} {
        return {mode: this.mode, axisLock: this.lock, following: this.modal !== null, dragging: this.drag !== null};
    }

    set(change: {position?: readonly number[]; yawDeg?: number; box?: readonly number[]}): void {
        const box = change.box ? localBox(change.box) : this.box;
        const next: BoxPose = {
            position: change.position ? vec3(change.position, "position") : this.at.position,
            yawDeg: change.yawDeg ?? this.at.yawDeg,
        };
        if (!Number.isFinite(next.yawDeg)) throw new Error("placeBox: yawDeg needs a finite number");
        // The handles are as long as the box is large: another box is drawn anew.
        const reshaped = box.some((v, i) => v !== this.box[i]);
        this.box = box;
        // A drag in progress is left alone: a form that follows the box says each pose back, and the
        // pointer's next move places the box again.
        this.at = next;
        if (reshaped) this.draw();
        else this.move();
    }

    setMode(mode: PlacedBoxMode): void {
        if (mode === "rotate" && !this.canTurn) return;
        if (mode === this.mode && this.lock === null) return;
        const changed = mode !== this.mode;
        this.mode = mode;
        // As the builder's: another gizmo starts unlocked, and a move that followed the pointer stays where it is.
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
            // Capture phase, as the builder's: a press on a handle is taken before the camera and the
            // scene's own click handlers see it.
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
    }

    /** Out of the viewport it is in: nothing drawn, no pointer and no key taken. Where it stands is kept. */
    detach(): void {
        if (!this.env) return;
        if (this.drag) this.env.input?.releasePointerCapture?.(this.drag.pointerId);
        this.drag = null;
        // A move that followed the pointer stays where it is; its lock goes with the pointer.
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

    private reach(): number {
        return handleReach(this.box);
    }

    /** What the gizmo is made of now, each as the segments it draws for where the box stands. */
    private handleSpecs(): {color: readonly [number, number, number]; widthPx: number; opacity?: number; segments: () => Float32Array}[] {
        const colour = (axis: GizmoAxis) => linearHex(HANDLE_AXIS_COLOR[axis]);
        const origin = () => this.at.position;
        if (this.modal) {
            const axis = this.modal.axis;
            return [{color: colour(axis), widthPx: 1, opacity: 0.6, segments: () => guideSegment(origin(), axis)}];
        }
        if (this.mode === "rotate") {
            return [{color: colour(2), widthPx: 3, segments: () => loopSegments(ringPoints(origin(), this.reach()))}];
        }
        return [
            ...this.moveAxes.map((axis) => ({color: colour(axis), widthPx: 3, segments: () => arrowSegments(origin(), axis, this.reach())})),
            {color: colour(2), widthPx: 2, segments: () => loopSegments(planeSquareCorners(origin(), this.reach()))},
        ];
    }

    /** Draw the box and its gizmo anew: another gizmo, another box, or the scene placed elsewhere. */
    private draw(): void {
        const layer = this.layer, env = this.env;
        if (!layer || !env) return;
        layer.clear();
        const offset = env.offset();
        const matrix = offset && (offset[0] || offset[1] || offset[2]) ? [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, offset[0], offset[1], offset[2], 1] : null;
        const color = linearColor(this.options.color ?? BOX_COLOR);
        // A marker in the scene, not a part of the model: never what the camera frames, never cut by a section.
        const look = {matrix, fitted: false, clipped: false};
        this.faces = layer.addMesh({positions: boxCorners(this.box, this.at), indices: BOX_FACE_INDICES, color, opacity: FACE_OPACITY, ...look});
        this.edges = layer.addLines({segments: boxEdgeSegments(this.box, this.at), color, widthPx: 2, onTop: true, ...look});
        this.handles = this.handleSpecs().map((spec) => ({
            handle: layer.addLines({segments: spec.segments(), color: spec.color, widthPx: spec.widthPx, opacity: spec.opacity, onTop: true, ...look}),
            segments: spec.segments,
        }));
    }

    /** The box stands somewhere else: the same things, moved. */
    private move(): void {
        const layer = this.layer;
        if (!layer) return;
        layer.setPositions(this.faces, boxCorners(this.box, this.at));
        layer.setPositions(this.edges, boxEdgeSegments(this.box, this.at));
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

    private along(origin: readonly number[], axis: GizmoAxis): Vec3 {
        const dir = axisVector(axis), reach = this.reach();
        return [origin[0] + dir[0] * reach, origin[1] + dir[1] * reach, origin[2] + dir[2] * reach];
    }

    /** The handle under `p`; null for none (the press is then the camera's). */
    private hit(p: ScreenPoint): Grab | null {
        if (this.modal) return null;
        const origin = this.at.position;
        if (this.mode === "rotate") {
            const ring = this.screen(ringPoints(origin, this.reach()));
            const near = ring.some((a, i) => {
                const b = ring[(i + 1) % ring.length];
                return a !== null && b !== null && distanceToSegment(p, a, b) <= GRAB_PX;
            });
            return near ? {kind: "turn"} : null;
        }
        const square = planeSquareCorners(origin, this.reach());
        const [o, ...rest] = this.screen([origin, ...this.moveAxes.map((axis) => this.along(origin, axis)), ...square]);
        if (!o) return null;
        let nearest: GizmoAxis | null = null;
        let within = GRAB_PX;
        for (let i = 0; i < this.moveAxes.length; i++) {
            const tip = rest[i];
            const distance = tip ? distanceToSegment(p, o, tip) : Infinity;
            if (distance <= within) {
                within = distance;
                nearest = this.moveAxes[i];
            }
        }
        if (nearest !== null) return {kind: "axis", axis: nearest};
        const corners = rest.slice(this.moveAxes.length);
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
        // A move that follows the pointer: the click puts the box down and lets the axis go.
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
        const [o, ex, ey, tip] = this.screen([origin, this.along(origin, 0), this.along(origin, 1), this.along(origin, grab.kind === "axis" ? grab.axis : 2)]);
        if (!o || !ex || !ey || !tip) return;
        const q0 = inPlane(p, o, ex, ey, this.reach()) ?? [0, 0];
        this.drag = {
            pointerId: e.pointerId,
            grab,
            start: copy(this.at),
            o,
            tip,
            ex,
            ey,
            t0: alongAxis(p, o, tip, this.reach()) ?? 0,
            q0,
            bearing: bearingOf(q0),
            turned: 0,
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
            const next = copy(drag.start);
            if (drag.grab.kind === "axis") {
                const t = alongAxis(p, drag.o, drag.tip, reach);
                if (t === null) return;
                next.position[drag.grab.axis] = snapTo(drag.start.position[drag.grab.axis] + t - drag.t0, this.step);
            } else {
                const q = inPlane(p, drag.o, drag.ex, drag.ey, reach);
                if (!q) return;
                if (drag.grab.kind === "plane") {
                    next.position[0] = snapTo(drag.start.position[0] + q[0] - drag.q0[0], this.step);
                    next.position[1] = snapTo(drag.start.position[1] + q[1] - drag.q0[1], this.step);
                } else {
                    const bearing = bearingOf(q);
                    drag.turned += turnBetween(drag.bearing, bearing);
                    drag.bearing = bearing;
                    // To a tenth of a degree, as the builder keeps a turn: it reads cleanly in a form.
                    // And in (-180, 180]: a turn past half way round is the same pose the short way.
                    next.yawDeg = turnBetween(0, Math.round((drag.start.yawDeg + snapTo(drag.turned, this.turnStep)) * 10) / 10);
                }
            }
            return this.userMoved(next);
        }
        const modal = this.modal;
        if (modal) {
            const [o, tip] = this.screen([modal.start.position, this.along(modal.start.position, modal.axis)]);
            const t = o && tip ? alongAxis(p, o, tip, this.reach()) : null;
            if (t === null) return;
            // The first move is where the pointer was when the axis was locked: the box does not jump to it.
            if (modal.startT === null) return void (modal.startT = t);
            const next = copy(modal.start);
            next.position[modal.axis] = snapTo(modal.start.position[modal.axis] + t - modal.startT, this.step);
            return this.userMoved(next);
        }
        // Over a handle the cursor says so; it is set back once, on leaving, and otherwise left to others.
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

    private userMoved(next: BoxPose): void {
        const same = next.yawDeg === this.at.yawDeg && next.position.every((v, i) => v === this.at.position[i]);
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

    /** End a move that follows the pointer: where the box is (`cancel` false), or back where it began. */
    private endModal(cancel: boolean): void {
        const modal = this.modal;
        if (!modal) return;
        this.modal = null;
        const style = this.env?.input?.style;
        if (style) style.cursor = "";
        if (cancel && modal.startT !== null) this.userMoved(modal.start);
    }

    private lockable(axis: GizmoAxis): boolean {
        return this.mode === "rotate" ? axis === 2 : this.moveAxes.includes(axis);
    }

    private onKeyDown(e: KeyLike): void {
        // A form field's keys are its own: a number typed for the box is not a gizmo key.
        if (isTextEntry(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
        const consume = () => {
            e.preventDefault();
            e.stopPropagation();
        };
        if (e.key === "Escape" || e.key === "Enter") {
            consume();
            // A move that follows the pointer first: Escape puts the box back, Enter puts it down.
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
        if (mode === "translate" || mode === "rotate") {
            consume();
            return this.setMode(mode);
        }
        const axis = gizmoAxisOfKey(key);
        if (axis === null) return;
        // The gizmo's key, whether this box can go that way or not: it is not also another shortcut.
        consume();
        if (!this.lockable(axis)) return;
        // Another axis mid-move puts the box down where it is and goes on from there.
        this.endModal(false);
        this.lock = toggledAxisLock(this.lock, axis);
        if (this.mode === "translate" && this.lock !== null) this.startModal(this.lock);
        this.draw();
    }
}

// ----- `ctx.scene.placeBox` -------------------------------------------------------------------------

let placements = 0;

function pageEnv(viewport: ViewportApi): BoxPlacementEnv {
    const page = typeof document === "undefined" ? null : document;
    const input = (page?.querySelector(`[${VIEWPORT_INPUT_ATTR}]`) ?? null) as unknown as BoxInput | null;
    if (!input) {
        // eslint-disable-next-line no-console
        console.warn("[plugins] placeBox: the view has no input surface: the box is drawn and cannot be dragged");
    }
    return {
        viewport,
        input,
        keys: typeof window === "undefined" ? null : (window as unknown as BoxKeys),
        offset: () => useSceneFrameStore.getState().offset,
        onOffsetChange: (listener) => useSceneFrameStore.subscribe(listener),
    };
}

/** Place a box in the scene until `end()` (or until `owner` is removed from the scene): drawn by the
 *  viewport on screen, and by the one that replaces it. */
export function placeBox(owner: string, options: PlaceBoxOptions): PlacedBox {
    const placement = new BoxPlacement(options, `plugin:${owner}:box:${++placements}`);
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
