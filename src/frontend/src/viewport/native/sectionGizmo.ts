// The section plane's handle on the three.js-free viewport: the active plane's outline, and an arrow
// along its normal that drags the plane. The planes themselves are the section store's (state/sectionStore),
// the same ones the three.js viewport's gizmo moves. No three.js here.
import {useSectionStore, type SectionPlane} from "@/state/sectionStore";
import type {NativeScene} from "./nativeScene";

type V3 = [number, number, number];
const add = (a: V3, b: V3, k = 1): V3 => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: V3): V3 => {
    const l = Math.hypot(a[0], a[1], a[2]) || 1;
    return [a[0] / l, a[1] / l, a[2] / l];
};

/** Linear rgb of the outline (the viewer's blue) and of the arrow (red, as the three.js gizmo's). */
const OUTLINE: V3 = [0.02, 0.13, 1];
const ARROW: V3 = [1, 0, 0];
/** A press this close to the arrow, in pixels, takes the drag. */
const GRAB_PX = 14;

/** What the gizmo draws for `plane` around a model box: the plane's outline and the arrow's ends. */
export function gizmoGeometry(plane: Pick<SectionPlane, "normal" | "constant">, box: readonly number[]): {outline: Float32Array; arrow: Float32Array; base: V3; tip: V3; length: number} {
    const n = unit([...plane.normal] as V3);
    const centre: V3 = [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2];
    const diagonal = Math.hypot(box[3] - box[0], box[4] - box[1], box[5] - box[2]) || 1;
    // The model's centre, moved onto the plane.
    const base = add(centre, n, -(dot(n, centre) + plane.constant));
    const u = unit(cross(n, Math.abs(n[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0]));
    const v = cross(n, u);
    const half = diagonal * 0.6;
    const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => add(add(base, u, a * half), v, b * half));
    const outline = new Float32Array(corners.flatMap((c, i) => [...c, ...corners[(i + 1) % 4]]));
    const length = diagonal * 0.2;
    const tip = add(base, n, length);
    const head = length * 0.18;
    const barbs = [u, [-u[0], -u[1], -u[2]] as V3].map((side) => add(add(tip, n, -head), side, head * 0.5));
    const arrow = new Float32Array([...base, ...tip, ...tip, ...barbs[0], ...tip, ...barbs[1]]);
    return {outline, arrow, base, tip, length};
}

/** How far along the arrow (in world units) a pointer move of (dx, dy) pixels is, for an arrow that
 *  spans `base` to `tip` on screen and `length` in the world. */
export function dragAlongArrow(dx: number, dy: number, base: {x: number; y: number}, tip: {x: number; y: number}, length: number): number {
    const ax = tip.x - base.x, ay = tip.y - base.y;
    const squared = ax * ax + ay * ay;
    // An arrow seen end-on has no direction on screen to drag along.
    return squared < 1 ? 0 : ((dx * ax + dy * ay) / squared) * length;
}

function distanceToSegment(p: {x: number; y: number}, a: {x: number; y: number}, b: {x: number; y: number}): number {
    const abx = b.x - a.x, aby = b.y - a.y;
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * abx + (p.y - a.y) * aby) / (abx * abx + aby * aby || 1)));
    return Math.hypot(p.x - (a.x + abx * t), p.y - (a.y + aby * t));
}

/** Show and drive the gizmo on `scene` for as long as the returned function has not been called. */
export function bindSectionGizmo(scene: NativeScene): () => void {
    let handles: number[] = [];
    let shown: {plane: SectionPlane; base: V3; tip: V3; length: number} | null = null;

    const draw = () => {
        for (const h of handles) scene.removeLines(h);
        handles = [];
        shown = null;
        const {planes, activeId, gizmoVisible} = useSectionStore.getState();
        const plane = gizmoVisible ? planes.find((p) => p.id === activeId && p.enabled) : undefined;
        const box = scene.modelBounds;
        if (!plane || !box) return;
        const g = gizmoGeometry(plane, box);
        handles = [scene.addLines(g.outline, OUTLINE, 1), scene.addLines(g.arrow, ARROW, 3)];
        shown = {plane, base: g.base, tip: g.tip, length: g.length};
    };

    scene.dragInterceptor = (e) => {
        if (!shown || e.button !== 0) return null;
        const base = scene.worldToClient(shown.base), tip = scene.worldToClient(shown.tip);
        if (!base || !tip || distanceToSegment({x: e.clientX, y: e.clientY}, base, tip) > GRAB_PX) return null;
        const {plane, length} = shown;
        const from = {x: e.clientX, y: e.clientY};
        // The plane keeps what is on its +normal side, at normal . x + constant = 0: moving it along the
        // normal by d lowers the constant by d.
        return (move) =>
            useSectionStore.getState().setConstant(plane.id, plane.constant - dragAlongArrow(move.clientX - from.x, move.clientY - from.y, base, tip, length));
    };

    draw();
    const unsubscribe = useSectionStore.subscribe(draw);
    scene.onLoaded.add(draw);
    return () => {
        unsubscribe();
        scene.onLoaded.delete(draw);
        scene.dragInterceptor = null;
        for (const h of handles) scene.removeLines(h);
        handles = [];
    };
}
