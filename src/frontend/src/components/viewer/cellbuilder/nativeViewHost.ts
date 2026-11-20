/**
 * The builder on the THREE.JS-FREE viewport (./viewHost.ts says what a host is).
 *
 * The builder's scene is the same three.js objects as on the three.js viewport, in a scene of its
 * own that nothing renders with three.js. This host
 *  - keeps a camera in step with the viewport's (its view and projection, perspective or
 *    orthographic), so picking, the gizmos and everything sized on screen work as there;
 *  - draws the scene through the viewport's overlays and billboards (viewport/sceneMirror.ts),
 *    before each frame the viewport draws and whenever the builder asks for a frame;
 *  - takes from the viewport's camera the presses the builder makes a drag of (a gizmo handle,
 *    a face, a resize handle) and, while the cells are edited, the selection rectangle, which
 *    selects cells there as it does on the three.js viewport (setupPointerHandler);
 *  - places the cells by the viewport's model offset, as the three.js viewport's model loader does
 *    for its own (sceneHelpers/setupModelLoader writes the offset the builder follows).
 */

import * as THREE from "three";
import {useCameraControlsStore} from "@/state/cameraControlsStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useModelState} from "@/state/modelState";
import {onRenderRequest} from "@/state/perfStore";
import {ndcRect, screenBoxMode} from "@/utils/bboxSelection/screenBox";
import {drawMarquee, marqueeElement} from "@/utils/bboxSelection/marquee";
import {mouseButtonOf, selectsByDrag} from "@/utils/camera/cameraMapping";
import {patchRaycaster, updateProjection} from "@/utils/scene/orthographicProjection";
import {onBeforeDraw} from "@/viewport/api/beforeDraw";
import {notifyViewportPick, onModelLoaded, type ViewportApi} from "@/viewport/api/viewportApi";
import {createSceneMirror} from "@/viewport/sceneMirror";
import type {TransformControls} from "three/examples/jsm/controls/TransformControls";
import {builderLayerEditing, claimPress, pressClaimed, selectCellsInScreenRect} from "./cellBoxSelect";
import {rebuild} from "./meshRebuild";
import type {CellBuilderScene} from "./sceneContext";
import type {BuilderViewHost, LoadedGeometry} from "./viewHost";

/** The name of the builder's overlay and billboard layers on the viewport. */
export const BUILDER_VIEW_LAYER = "cellbuilder";

const FALLBACK_FOV = 45;
const inverse = new THREE.Matrix4();

/**
 * Make `camera` draw as a viewport's camera does: `view` and `proj` as `ViewportApi.cameraMatrices`
 * gives them (column-major, clip depth 0..1), `fovDeg` its vertical field of view. An orthographic
 * projection becomes this camera's orthographic mode (utils/scene/orthographicProjection), the one
 * the three.js viewport draws with, so rays, gizmo and handle sizes follow that mode's rules.
 */
export function followViewportCamera(camera: THREE.PerspectiveCamera, view: ArrayLike<number>, proj: ArrayLike<number>, fovDeg: number): void {
    inverse.fromArray(Array.from(view)).invert();
    inverse.decompose(camera.position, camera.quaternion, camera.scale);
    camera.updateMatrixWorld(true);
    const orthographic = proj[11] === 0;
    camera.fov = fovDeg;
    camera.zoom = 1;
    camera.aspect = proj[0] ? proj[5] / proj[0] : 1;
    if (orthographic) {
        const near = proj[14] / proj[10];
        const far = near - 1 / proj[10];
        camera.near = 0.01;
        camera.far = Math.max(Math.abs(near), Math.abs(far), 1);
        // The view's height in the world, shown by a perspective view of this field at a distance.
        const height = 2 / proj[5];
        updateProjection(camera, true, height / (2 * Math.tan((fovDeg * Math.PI) / 360)));
    } else {
        const near = proj[14] / proj[10];
        const far = proj[14] / (proj[10] + 1);
        camera.near = near > 0 && Number.isFinite(near) ? near : 0.01;
        camera.far = far > camera.near && Number.isFinite(far) ? far : 1e7;
        updateProjection(camera, false, 0);
    }
}

/** The builder's host on the three.js-free viewport `viewport`. */
export function nativeViewHost(viewport: ViewportApi): BuilderViewHost {
    // Rays through an orthographic view of the camera below are parallel, as on the three.js viewport.
    patchRaycaster();
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(FALLBACK_FOV, 1, 0.1, 1000);
    camera.up.set(0, 0, 1);
    const elements = viewport.elements?.();
    const view = elements?.view ?? document.body;
    const input = elements?.input ?? view;
    const fov = () => viewport.cameraPose()?.fovDeg ?? FALLBACK_FOV;
    const follow = () => {
        const m = viewport.cameraMatrices?.();
        if (m) followViewportCamera(camera, m.view, m.proj, fov());
    };
    follow();
    return {
        kind: "native",
        scene,
        camera: () => camera,
        input: () => input,
        rect: () => view.getBoundingClientRect(),
        // The camera is held for a drag at its press instead (`takePresses` below): this viewport
        // asks before it moves the camera for one, and is not told to stop halfway.
        setCameraEnabled: () => {},
        loadedGeometry: (name) => loadedGeometry(viewport, name),
        setGroundGridHidden: (hidden) => viewport.setGroundGridHidden?.(hidden),
        bind: (ctx) => bindNativeView(ctx, viewport, camera, view, follow, fov),
    };
}

/** The viewport's objects called `name` as one piece of loaded geometry: their boxes together, and
 *  the ends of their outlines as the viewport draws them (`featureEdges`) for its points, else the
 *  box's corners. */
export function loadedGeometry(viewport: Pick<ViewportApi, "objectsNamed" | "featureEdges">, name: string): LoadedGeometry | null {
    const found = viewport.objectsNamed?.(name) ?? [];
    if (!found.length) return null;
    const box = new THREE.Box3();
    for (const o of found) {
        box.expandByPoint(new THREE.Vector3(...o.min));
        box.expandByPoint(new THREE.Vector3(...o.max));
    }
    return {
        box,
        points: (max) => {
            const all: THREE.Vector3[] = [];
            for (const o of found) {
                const edges = viewport.featureEdges?.(o.ref);
                if (edges) for (let i = 0; i + 2 < edges.length; i += 3) all.push(new THREE.Vector3(edges[i], edges[i + 1], edges[i + 2]));
            }
            if (!all.length) {
                for (let k = 0; k < 8; k++) {
                    all.push(new THREE.Vector3(k & 1 ? box.max.x : box.min.x, k & 2 ? box.max.y : box.min.y, k & 4 ? box.max.z : box.min.z));
                }
            }
            const stride = Math.max(1, Math.floor(all.length / max));
            return stride === 1 ? all : all.filter((_, i) => i % stride === 0);
        },
    };
}

/** Whether a press lands on a handle of `gizmo` (finding which, as the gizmo does on a hover): on a
 *  touch screen nothing hovered before the press. */
function overGizmo(gizmo: TransformControls, helper: THREE.Object3D, e: PointerEvent, rect: DOMRect): boolean {
    if (!gizmo.object || !gizmo.enabled || gizmo.dragging || !helper.visible) return false;
    if (gizmo.axis) return true;
    const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    (gizmo as unknown as {pointerHover(p: {x: number; y: number; button: number}): void}).pointerHover({x, y, button: e.button});
    return !!gizmo.axis;
}

function bindNativeView(
    ctx: CellBuilderScene,
    viewport: ViewportApi,
    camera: THREE.PerspectiveCamera,
    view: HTMLElement,
    follow: () => void,
    fov: () => number,
): () => void {
    let live = true;
    const overlays = viewport.overlays(BUILDER_VIEW_LAYER);
    const billboards = viewport.billboards(BUILDER_VIEW_LAYER);
    const mirror = createSceneMirror(ctx.scene, camera, {
        overlays,
        billboards,
        screen: () => ({height: view.clientHeight, fovDeg: fov()}),
        // The renderer lights an overlay mesh as it lights the model (a "Show as CAD" preview).
        litOverlays: true,
    });
    // Before each frame the viewport draws (the camera has moved for it): the camera, then the scene
    // as it is now -- the gizmos and handles are sized from the camera as the scene is brought up to date.
    const draw = () => {
        follow();
        mirror.sync();
    };
    draw();
    const offDraw = onBeforeDraw(draw);
    // Whatever the builder changes asks for a frame (`requestRender`): what changed is handed to the
    // viewport, which draws a frame for it.
    let queued = 0;
    const schedule = () => {
        if (!queued) queued = requestAnimationFrame(() => {
            queued = 0;
            mirror.sync();
        });
    };
    const offRequest = onRenderRequest(schedule);
    // A gizmo's hover highlight changes on a move, with no request of its own.
    ctx.gizmo.addEventListener("change", schedule);
    ctx.portGizmo.addEventListener("change", schedule);

    // The model offset: what the viewport placed the model that replaced what it showed by. The
    // three.js viewport's loader writes its own the same way, and the cells follow it (syncOffset).
    const followOffset = (sourceName: string): boolean => {
        const o = viewport.sourceOrigin(sourceName);
        if (!o) return false;
        const t = useModelState.getState().translation;
        if (!t || t.x !== o[0] || t.y !== o[1] || t.z !== o[2]) useModelState.getState().setTranslation(new THREE.Vector3(o[0], o[1], o[2]));
        return true;
    };
    for (const sourceName of useModelState.getState().loadedSourceNames) if (followOffset(sourceName)) break;
    const offLoaded = onModelLoaded((info) => {
        if (!info.added) followOffset(info.sourceName);
        // A CAD-backed equipment box fits the model just loaded (initScene refits on the loaded
        // sources too, which may be told before this viewport has the model).
        if (useCellBuilderStore.getState().equipmentCad) requestAnimationFrame(() => void (live && rebuild(ctx)));
    });

    const takeRectangle = (e: PointerEvent, layer: THREE.Object3D): ((move: PointerEvent) => void) | null => {
        const button = e.pointerType === "mouse" ? mouseButtonOf(e.button) : null;
        if (!button || !selectsByDrag(useCameraControlsStore.getState().mapping, button, e)) return null;
        const start = {x: e.clientX, y: e.clientY};
        const add = e.shiftKey || e.ctrlKey || e.metaKey;
        let el: HTMLDivElement | null = null;
        const end = (up: PointerEvent) => {
            if (up.pointerId !== e.pointerId) return;
            window.removeEventListener("pointerup", end, true);
            window.removeEventListener("pointercancel", end, true);
            if (!el) {
                // A press that did not move is a click: the builder's own handlers pick a cell; the
                // model's object under it is picked as any click picks it (the three.js viewport's
                // click handler runs for it too).
                if (up.type === "pointerup" && button === "left") void clickThrough(up, add);
                return;
            }
            el.remove();
            if (up.type !== "pointerup") return;
            const rect = ndcRect(start, {x: up.clientX, y: up.clientY}, view.getBoundingClientRect());
            selectCellsInScreenRect(layer, camera, rect, screenBoxMode(start.x, up.clientX), add);
        };
        window.addEventListener("pointerup", end, true);
        window.addEventListener("pointercancel", end, true);
        return (move) => {
            if (!el && Math.hypot(move.clientX - start.x, move.clientY - start.y) <= 5) return;
            el ??= marqueeElement(view);
            drawMarquee(el, view, start, {x: move.clientX, y: move.clientY});
        };
    };
    const clickThrough = async (up: PointerEvent, add: boolean) => {
        const rect = view.getBoundingClientRect();
        const hit = (await viewport.surfaceAt?.(up.clientX - rect.left, up.clientY - rect.top)) ?? null;
        const ref = hit?.ref ?? null;
        if (ref) viewport.setSelection([ref], {add});
        else if (!add) viewport.clearSelection();
        notifyViewportPick({ref, point: null});
    };
    const held = () => {};
    const offPresses = viewport.takePresses?.((e) => {
        // Gone, though another taker asked after this one kept it in the chain.
        if (!live) return null;
        // A press on a gizmo's handle is the gizmo's drag. The builder's own press handler (on the
        // capture pass, before this) marks one the pointer was over already.
        const rect = view.getBoundingClientRect();
        if (e.button === 0 && !pressClaimed(e) && (overGizmo(ctx.gizmo, ctx.gizmoHelper, e, rect) || overGizmo(ctx.portGizmo, ctx.portGizmoHelper, e, rect))) {
            claimPress(e);
        }
        // A face drag or a resize handle's: the builder moves the box, the camera stands still.
        if (pressClaimed(e) || ctx.drag?.pointerId === e.pointerId) return held;
        const layer = builderLayerEditing(ctx.scene);
        return layer ? takeRectangle(e, layer) : null;
    }) ?? (() => {});

    return () => {
        live = false;
        offPresses();
        offLoaded();
        offRequest();
        offDraw();
        ctx.gizmo.removeEventListener("change", schedule);
        ctx.portGizmo.removeEventListener("change", schedule);
        if (queued) cancelAnimationFrame(queued);
        mirror.dispose();
        overlays.dispose();
        billboards.dispose();
    };
}
