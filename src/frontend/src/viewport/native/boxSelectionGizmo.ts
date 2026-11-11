// Selection boxes on the three.js-free viewport: each box's edges, the active box's handles (one per
// face to resize it, an arrow per axis to move it), the rectangle dragged on the screen with Shift,
// and a box's right-click menu. The boxes are the box store's (state/bboxSelectionStore), the same
// ones the three.js viewport's controller draws, and what a box selects is asked through the viewport
// interface (utils/bboxSelection/applyBoxSelection). No three.js here.
//
// The edges are helper lines of the renderer, drawn on top of the model, and the faces see-through
// triangles in the box's colour. The handles are an SVG over the view, placed from the camera after
// every frame, so they keep their size on screen; a press is hit-tested against them on screen,
// before the camera sees it (`dragInterceptor`).
import {useBBoxSelectionStore, type SelectionBox} from "@/state/bboxSelectionStore";
import {setSceneOffset, useSceneFrameStore} from "@/state/sceneFrameStore";
import {bindActiveBoxSelection, selectInScreenRect} from "@/utils/bboxSelection/applyBoxSelection";
import {bindBBoxSelectionStorage} from "@/utils/bboxSelection/boxPersistence";
import {axisVector, boxEdges, boxFaces, faceAxis, HANDLE_COLORS, lineParamAtScreen, minBoxSize, moveArrows, screenPointOverBox, toScreen} from "@/utils/bboxSelection/boxHandles";
import {BOX_FACES, boxCenter, centeredAt, faceCenter, moveFace, type Aabb, type Vec3} from "@/utils/bboxSelection/boxMath";
import {drawMarquee, marqueeElement} from "@/utils/bboxSelection/marquee";
import {ndcRect, screenBoxMode} from "@/utils/bboxSelection/screenRect";
import type {NativeScene} from "./nativeScene";

const SVG = "http://www.w3.org/2000/svg";
/** A face handle's radius, and how near a press must be to a handle or an arrow to take it (pixels). */
const HANDLE_RADIUS_PX = 7;
const GRAB_HANDLE_PX = 11;
const GRAB_ARROW_PX = 8;
/** A move arrow's length, as a share of the distance from the camera to the box's centre. */
const ARROW_LENGTH = 0.14;
/** A press released within this many pixels of where it went down is a click, not a rectangle. */
const CLICK_SLOP_PX = 5;

/** "#rrggbb" (sRGB) as linear rgb, which the renderer takes. */
function linearRgb(hex: string): [number, number, number] {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!m) return [0.03, 0.4, 1];
    const linear = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return [linear(parseInt(m[1], 16) / 255), linear(parseInt(m[2], 16) / 255), linear(parseInt(m[3], 16) / 255)];
}

function distanceToSegment(p: {x: number; y: number}, a: {x: number; y: number}, b: {x: number; y: number}): number {
    const abx = b.x - a.x, aby = b.y - a.y;
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * abx + (p.y - a.y) * aby) / (abx * abx + aby * aby || 1)));
    return Math.hypot(p.x - (a.x + abx * t), p.y - (a.y + aby * t));
}

/** What a press took: a face of the box to move along its axis, or the whole box along an axis. */
type Grab = {kind: "face"; face: number} | {kind: "move"; axis: number};

/** Draw and drive the selection boxes on `scene` for as long as the returned function has not been called. */
export function bindBoxSelectionGizmo(scene: NativeScene): () => void {
    const {view, input} = scene.elements;
    /** What is drawn for each box, by its id: the handles of its edges and its faces. */
    const drawn = new Map<string, number[]>();
    /** The active box's bounds while a drag is live, before they are committed. */
    let live: Aabb | null = null;

    const overlay = document.createElementNS(SVG, "svg");
    overlay.setAttribute("data-box-selection-handles", "true");
    Object.assign(overlay.style, {position: "absolute", inset: "0", width: "100%", height: "100%", pointerEvents: "none", zIndex: "5", display: "none"});
    const arrows = [0, 1, 2].map((axis) => {
        const line = document.createElementNS(SVG, "line");
        line.setAttribute("stroke", HANDLE_COLORS[axis]);
        line.setAttribute("stroke-width", "3");
        line.setAttribute("stroke-linecap", "round");
        line.setAttribute("data-box-move", "xyz"[axis]);
        const head = document.createElementNS(SVG, "circle");
        head.setAttribute("r", "4");
        head.setAttribute("fill", HANDLE_COLORS[axis]);
        overlay.append(line, head);
        return {line, head};
    });
    const dots = BOX_FACES.map((f, face) => {
        const dot = document.createElementNS(SVG, "circle");
        dot.setAttribute("r", String(HANDLE_RADIUS_PX));
        dot.setAttribute("fill", HANDLE_COLORS[f.axis]);
        dot.setAttribute("fill-opacity", "0.9");
        dot.setAttribute("data-box-face", String(face));
        overlay.append(dot);
        return dot;
    });
    view.appendChild(overlay);

    const activeBox = (): SelectionBox | null => {
        const st = useBBoxSelectionStore.getState();
        const box = st.boxes.find((b) => b.id === st.activeId);
        return box && box.visible ? box : null;
    };
    /** The active box as it is drawn now: mid-drag, the bounds the drag has reached. */
    const shownBounds = (): Aabb | null => live ?? activeBox();

    /** A world point in the view's own pixels; null behind the camera. */
    const toView = (m: ArrayLike<number>, p: ArrayLike<number>, rect: DOMRect): {x: number; y: number} | null => {
        const s = toScreen(m, p);
        return s ? {x: (s.x * 0.5 + 0.5) * rect.width, y: (0.5 - s.y * 0.5) * rect.height} : null;
    };
    const arrowLength = (b: Aabb): number => {
        const eye = scene.cameraPose.eye, c = boxCenter(b);
        return Math.hypot(eye[0] - c[0], eye[1] - c[1], eye[2] - c[2]) * ARROW_LENGTH;
    };

    /** Put the handles where the camera shows the active box now. */
    const placeHandles = () => {
        const b = shownBounds();
        if (!b) {
            overlay.style.display = "none";
            return;
        }
        overlay.style.display = "";
        const rect = view.getBoundingClientRect();
        const m = scene.viewProjection();
        dots.forEach((dot, face) => {
            const p = toView(m, faceCenter(b, face), rect);
            dot.style.display = p ? "" : "none";
            if (p) {
                dot.setAttribute("cx", p.x.toFixed(1));
                dot.setAttribute("cy", p.y.toFixed(1));
            }
        });
        for (const {axis, base, tip} of moveArrows(b, arrowLength(b))) {
            const from = toView(m, base, rect), to = toView(m, tip, rect);
            const {line, head} = arrows[axis];
            line.style.display = head.style.display = from && to ? "" : "none";
            if (!from || !to) continue;
            line.setAttribute("x1", from.x.toFixed(1));
            line.setAttribute("y1", from.y.toFixed(1));
            line.setAttribute("x2", to.x.toFixed(1));
            line.setAttribute("y2", to.y.toFixed(1));
            head.setAttribute("cx", to.x.toFixed(1));
            head.setAttribute("cy", to.y.toFixed(1));
        }
    };

    const drawBox = (box: SelectionBox, bounds: Aabb, active: boolean) => {
        for (const old of drawn.get(box.id) ?? []) scene.removeLines(old);
        drawn.delete(box.id);
        if (!box.visible) return;
        const color = linearRgb(box.color);
        const helpers = [scene.addLines(boxEdges(bounds), color, active ? 2 : 1)];
        // The faces in the box's colour at its opacity; the edges stay opaque.
        if (box.opacity > 0) {
            const {points, indices} = boxFaces(bounds);
            helpers.push(scene.addFaces(points, indices, color, box.opacity));
        }
        drawn.set(box.id, helpers);
    };

    /** Every box's edges and the active one's handles, from the store. */
    const rebuild = () => {
        const st = useBBoxSelectionStore.getState();
        for (const helpers of drawn.values()) for (const handle of helpers) scene.removeLines(handle);
        drawn.clear();
        for (const box of st.boxes) drawBox(box, box.id === st.activeId && live ? live : box, box.id === st.activeId);
        placeHandles();
    };

    const showLive = (b: Aabb) => {
        live = b;
        const box = activeBox();
        if (box) drawBox(box, b, true);
        placeHandles();
    };

    const commit = () => {
        const b = live;
        live = null;
        const id = useBBoxSelectionStore.getState().activeId;
        if (b && id) useBBoxSelectionStore.getState().update(id, {min: [...b.min], max: [...b.max]});
    };

    /** The pointer in the view's normalised device coordinates, and the view's proportions. */
    const pointer = (e: {clientX: number; clientY: number}) => {
        const rect = view.getBoundingClientRect();
        return {
            rect,
            local: {x: e.clientX - rect.left, y: e.clientY - rect.top},
            x: ((e.clientX - rect.left) / rect.width) * 2 - 1,
            y: 1 - ((e.clientY - rect.top) / rect.height) * 2,
            aspect: rect.width / Math.max(1, rect.height),
        };
    };

    /** The handle or arrow under a press, nearest first; null when it is on neither. */
    const grabAt = (e: PointerEvent, b: Aabb): Grab | null => {
        const at = pointer(e);
        const m = scene.viewProjection();
        let best: {grab: Grab; distance: number} | null = null;
        for (let face = 0; face < BOX_FACES.length; face++) {
            const p = toView(m, faceCenter(b, face), at.rect);
            const distance = p ? Math.hypot(p.x - at.local.x, p.y - at.local.y) : Infinity;
            if (distance <= GRAB_HANDLE_PX && (!best || distance < best.distance)) best = {grab: {kind: "face", face}, distance};
        }
        if (best) return best.grab;
        for (const {axis, base, tip} of moveArrows(b, arrowLength(b))) {
            const from = toView(m, base, at.rect), to = toView(m, tip, at.rect);
            const distance = from && to ? distanceToSegment(at.local, from, to) : Infinity;
            if (distance <= GRAB_ARROW_PX && (!best || distance < best.distance)) best = {grab: {kind: "move", axis}, distance};
        }
        return best?.grab ?? null;
    };

    /** Until the pointer that started a drag is released: `done` runs once, with the release. */
    const untilRelease = (pointerId: number, done: (e: PointerEvent) => void) => {
        const end = (e: PointerEvent) => {
            if (e.pointerId !== pointerId) return;
            window.removeEventListener("pointerup", end, true);
            window.removeEventListener("pointercancel", end, true);
            done(e);
        };
        window.addEventListener("pointerup", end, true);
        window.addEventListener("pointercancel", end, true);
    };

    /** A press on a handle of the active box: the drag that resizes or moves it. */
    const dragHandle = (e: PointerEvent): ((move: PointerEvent) => void) | null => {
        const box = activeBox();
        if (!box || e.button !== 0) return null;
        const start: Aabb = {min: [...box.min], max: [...box.max]};
        const grab = grabAt(e, start);
        if (!grab) return null;
        const axis = grab.kind === "face" ? faceAxis(grab.face) : grab.axis;
        const origin: Vec3 = grab.kind === "face" ? faceCenter(start, grab.face) : boxCenter(start);
        const dir = axisVector(axis);
        // The camera stands still for the drag: its matrix at the press serves every move.
        const m = scene.viewProjection();
        const at = pointer(e);
        const startT = lineParamAtScreen(m, origin, dir, at.x, at.y, at.aspect);
        if (startT === null) return null;
        untilRelease(e.pointerId, commit);
        return (move) => {
            const now = pointer(move);
            const t = lineParamAtScreen(m, origin, dir, now.x, now.y, now.aspect);
            if (t === null) return;
            if (grab.kind === "face") {
                showLive(moveFace(start, grab.face, t - startT, minBoxSize(start)));
            } else {
                const centre = boxCenter(start);
                centre[axis] += t - startT;
                showLive(centeredAt(start, centre));
            }
        };
    };

    /** Shift + left drag: a rectangle on the screen, rightwards a window, leftwards a crossing. A
     *  Shift press that does not move stays what it was, a click that adds what is under it. */
    const dragRectangle = (e: PointerEvent): ((move: PointerEvent) => void) | null => {
        if (e.button !== 0 || !e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return null;
        const start = {x: e.clientX, y: e.clientY};
        let el: HTMLDivElement | null = null;
        untilRelease(e.pointerId, (up) => {
            if (!el) {
                if (up.type !== "pointerup") return;
                void scene.pick(up.clientX, up.clientY).then((id) => scene.pickSelect(id, true));
                return;
            }
            el.remove();
            if (up.type !== "pointerup") return;
            selectInScreenRect(ndcRect(start, {x: up.clientX, y: up.clientY}, view.getBoundingClientRect()), screenBoxMode(start.x, up.clientX));
        });
        return (move) => {
            if (!el && Math.hypot(move.clientX - start.x, move.clientY - start.y) <= CLICK_SLOP_PX) return;
            el ??= marqueeElement(view);
            drawMarquee(el, view, start, {x: move.clientX, y: move.clientY});
        };
    };

    // A press goes to a handle first, then to the rectangle, then to whoever had the drags before
    // (the section plane's arrow), and only then to the camera.
    const before = scene.dragInterceptor;
    const intercept = (e: PointerEvent) => dragHandle(e) ?? dragRectangle(e) ?? before?.(e) ?? null;
    scene.dragInterceptor = intercept;

    // A right-click on a box opens its menu (modes, reselect, colour, hide, delete): the active box
    // first, when several are under the pointer.
    const onContextMenu = (e: MouseEvent) => {
        const st = useBBoxSelectionStore.getState();
        const at = pointer(e);
        const m = scene.viewProjection();
        const over = st.boxes.filter((b) => b.visible && screenPointOverBox(m, b, at.x, at.y));
        const box = over.find((b) => b.id === st.activeId) ?? over[0];
        if (box) st.openMenu({boxId: box.id, x: e.clientX, y: e.clientY});
    };
    input.addEventListener("contextmenu", onContextMenu);
    // A Shift press would otherwise stretch the page's text selection to the pointer.
    const keepText = (e: MouseEvent) => void (e.shiftKey && e.preventDefault());
    input.addEventListener("mousedown", keepText);

    rebuild();
    const unsubscribe = useBBoxSelectionStore.subscribe((s, prev) => {
        if (s.boxes !== prev.boxes || s.activeId !== prev.activeId) rebuild();
    });
    // The active box's selection follows its bounds and mode (after the box is redrawn).
    const unfollow = bindActiveBoxSelection();
    // A load that replaces the model empties the renderer's scene, the helper lines with it: they
    // are drawn again. What the boxes select is left alone, as on the three.js viewport ("Reselect"
    // in a box's menu takes what a new model has inside it).
    scene.onLoaded.add(rebuild);
    scene.onFrame.add(placeHandles);

    // The boxes are kept in this browser while the viewport is up, as on the three.js viewport. This
    // viewport draws a model where its file puts it, so once one is on screen the world's offset from
    // the models' own coordinates is zero: boxes stored under another offset move over.
    const placed = () => {
        const offset = useSceneFrameStore.getState().offset;
        if (scene.modelBounds && (!offset || offset.some((v) => v !== 0))) setSceneOffset([0, 0, 0]);
    };
    const unbindStorage = bindBBoxSelectionStorage();
    placed();
    scene.onLoaded.add(placed);

    return () => {
        scene.onLoaded.delete(placed);
        unbindStorage();
        scene.onFrame.delete(placeHandles);
        scene.onLoaded.delete(rebuild);
        unfollow();
        unsubscribe();
        input.removeEventListener("contextmenu", onContextMenu);
        input.removeEventListener("mousedown", keepText);
        if (scene.dragInterceptor === intercept) scene.dragInterceptor = before;
        for (const helpers of drawn.values()) for (const handle of helpers) scene.removeLines(handle);
        drawn.clear();
        overlay.remove();
    };
}
