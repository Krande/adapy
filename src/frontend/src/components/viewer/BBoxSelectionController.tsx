import React from "react";
import * as THREE from "three";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";

import {useViewerRefs} from "@/state/AdaViewerContext";
import {useBBoxSelectionStore, type SelectionBox} from "@/state/bboxSelectionStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {applyBoxSelection} from "@/utils/bboxSelection/applyBoxSelection";
import {BOX_FACES, boxCenter, boxSize, centeredAt, faceCenter, moveFace, type Aabb, type Vec3} from "@/utils/bboxSelection/boxMath";
import {gpuMeshPicker} from "@/utils/mesh_select/GpuMeshPicker";
import {lineParamFromRay} from "./cellbuilder/geometryHelpers";

// Headless: draws the selection boxes, a move gizmo and six face handles on the active
// box, and re-applies the active box's selection whenever its bounds or mode change.

const HANDLE_COLOR = [0xe5484d, 0x30a46c, 0x3e63dd] as const;
/** Handle radius as a fraction of its distance from the camera: constant on screen. */
const HANDLE_SCREEN_SIZE = 0.012;
const BOX_RENDER_ORDER = 9990;

/** The drawn box meshes, for the right-click pick in setupPointerHandler. */
let boxPickTargets: THREE.Mesh[] = [];

/** The selection box under the cursor, when it is nearer than the geometry there. */
export function pickSelectionBox(clientX: number, clientY: number): string | null {
    const {camera: cameraRef, renderer: rendererRef} = getViewerRuntime();
    const camera = cameraRef.current;
    const renderer = rendererRef.current;
    if (!camera || !renderer || boxPickTargets.length === 0) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.layers.set(1);
    ray.setFromCamera(
        new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1),
        camera,
    );
    const hit = ray.intersectObjects(boxPickTargets.filter((m) => m.visible), false)[0];
    if (!hit) return null;
    let surface: ReturnType<typeof gpuMeshPicker.pickAt> = null;
    try {
        surface = gpuMeshPicker.pickAt(clientX, clientY);
    } catch {
        surface = null;
    }
    if (surface && camera.position.distanceTo(surface.worldPosition) < hit.distance) return null;
    return (hit.object.userData.__bboxId as string | undefined) ?? null;
}

const BBoxSelectionController: React.FC = () => {
    const {scene: sceneRef, camera: cameraRef, renderer: rendererRef} = useViewerRefs();
    React.useEffect(() => {
        let cleanup: (() => void) | null = null;
        let raf = 0;
        const tryInit = () => {
            const renderer = rendererRef.current;
            const scene = sceneRef.current;
            const camera = cameraRef.current;
            if (!renderer || !scene || !camera) {
                raf = requestAnimationFrame(tryInit);
                return;
            }
            cleanup = init(renderer, scene, camera);
        };
        tryInit();
        return () => {
            cancelAnimationFrame(raf);
            cleanup?.();
        };
    }, []);
    return null;
};

interface BoxVisual {
    mesh: THREE.Mesh;
    edges: THREE.LineSegments;
}

interface FaceDrag {
    face: number;
    startBox: Aabb;
    lineOrigin: THREE.Vector3;
    lineDir: THREE.Vector3;
    startT: number;
    pointerId: number;
}

function init(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera): () => void {
    const container = new THREE.Group();
    container.name = "__bbox_selection__";
    container.userData.__excludeFromFit = true;
    scene.add(container);

    const unitBox = new THREE.BoxGeometry(1, 1, 1);
    const unitEdges = new THREE.EdgesGeometry(unitBox);
    const visuals = new Map<string, BoxVisual>();

    const handleGeometry = new THREE.SphereGeometry(1, 16, 12);
    const handles = BOX_FACES.map((f, i) => {
        const m = new THREE.Mesh(
            handleGeometry,
            new THREE.MeshBasicMaterial({color: HANDLE_COLOR[f.axis], depthTest: false, transparent: true, opacity: 0.9}),
        );
        m.renderOrder = BOX_RENDER_ORDER + 2;
        m.layers.set(1);
        m.userData.__bboxFace = i;
        m.visible = false;
        m.onBeforeRender = (_r, _s, cam) => {
            m.scale.setScalar(Math.max(1e-6, cam.position.distanceTo(m.position) * HANDLE_SCREEN_SIZE));
        };
        container.add(m);
        return m;
    });

    const proxy = new THREE.Object3D();
    proxy.userData.__excludeFromFit = true;
    scene.add(proxy);
    const gizmo = new TransformControls(camera, renderer.domElement);
    gizmo.setMode("translate");
    gizmo.setSpace("world");
    const gizmoHelper = gizmo.getHelper();
    gizmoHelper.visible = false;
    gizmoHelper.userData.__excludeFromFit = true;
    scene.add(gizmoHelper);

    /** The active box's bounds while a drag is live, before they are committed. */
    let live: Aabb | null = null;
    let drag: FaceDrag | null = null;
    /** A drag ended on the canvas: its click must not reach the scene's selection picking. */
    let swallowClick = false;
    const raycaster = new THREE.Raycaster();
    raycaster.layers.set(1);

    const activeBox = (): SelectionBox | null => {
        const st = useBBoxSelectionStore.getState();
        return st.boxes.find((b) => b.id === st.activeId) ?? null;
    };

    const placeVisual = (v: BoxVisual, b: Aabb) => {
        const s = boxSize(b);
        const c = boxCenter(b);
        for (const o of [v.mesh, v.edges]) {
            o.position.set(c[0], c[1], c[2]);
            o.scale.set(Math.max(s[0], 1e-6), Math.max(s[1], 1e-6), Math.max(s[2], 1e-6));
        }
    };

    const placeHandles = (b: Aabb | null) => {
        handles.forEach((h, i) => {
            h.visible = !!b;
            if (!b) return;
            const p = faceCenter(b, i);
            h.position.set(p[0], p[1], p[2]);
        });
    };

    const disposeVisual = (v: BoxVisual) => {
        (v.mesh.material as THREE.Material).dispose();
        (v.edges.material as THREE.Material).dispose();
        container.remove(v.mesh, v.edges);
    };

    const rebuild = () => {
        const st = useBBoxSelectionStore.getState();
        const runtimeCam = getViewerRuntime().camera.current;
        if (runtimeCam) gizmo.camera = runtimeCam;
        for (const [id, v] of visuals) {
            if (!st.boxes.some((b) => b.id === id)) {
                disposeVisual(v);
                visuals.delete(id);
            }
        }
        for (const box of st.boxes) {
            let v = visuals.get(box.id);
            if (!v) {
                const mesh = new THREE.Mesh(
                    unitBox,
                    new THREE.MeshBasicMaterial({transparent: true, depthWrite: false, side: THREE.DoubleSide}),
                );
                mesh.renderOrder = BOX_RENDER_ORDER;
                mesh.layers.set(1);
                mesh.userData.__bboxId = box.id;
                const edges = new THREE.LineSegments(unitEdges, new THREE.LineBasicMaterial());
                edges.renderOrder = BOX_RENDER_ORDER + 1;
                edges.layers.set(1);
                edges.raycast = () => {};
                container.add(mesh, edges);
                v = {mesh, edges};
                visuals.set(box.id, v);
            }
            const mat = v.mesh.material as THREE.MeshBasicMaterial;
            mat.color.set(box.color);
            mat.opacity = box.opacity;
            (v.edges.material as THREE.LineBasicMaterial).color.set(box.color);
            v.mesh.visible = v.edges.visible = box.visible;
            placeVisual(v, box.id === st.activeId && live ? live : box);
        }
        boxPickTargets = [...visuals.values()].map((v) => v.mesh);

        const active = activeBox();
        const editable = active && active.visible ? active : null;
        if (editable && !drag) placeHandles(live ?? editable);
        else if (!editable) placeHandles(null);
        if (editable) {
            if (!gizmo.dragging) {
                const c = boxCenter(editable);
                proxy.position.set(c[0], c[1], c[2]);
            }
            if (gizmo.object !== proxy) gizmo.attach(proxy);
            gizmo.enabled = true;
            gizmoHelper.visible = true;
        } else {
            gizmo.detach();
            gizmo.enabled = false;
            gizmoHelper.visible = false;
        }
        requestRender();
    };

    const showLive = (b: Aabb) => {
        live = b;
        const id = useBBoxSelectionStore.getState().activeId;
        const v = id ? visuals.get(id) : undefined;
        if (v) placeVisual(v, b);
        placeHandles(b);
        requestRender();
    };

    const commit = (b: Aabb) => {
        live = null;
        const id = useBBoxSelectionStore.getState().activeId;
        if (id) useBBoxSelectionStore.getState().update(id, {min: [...b.min], max: [...b.max]});
    };

    const setControlsEnabled = (on: boolean) => {
        const ctl = getViewerRuntime().controls.current;
        if (ctl) ctl.enabled = on;
    };

    gizmo.addEventListener("dragging-changed", (e: any) => {
        setControlsEnabled(!e.value);
        if (e.value) {
            swallowClick = true;
            return;
        }
        if (live) commit(live);
        requestRender();
    });
    gizmo.addEventListener("objectChange", () => {
        const box = activeBox();
        if (!box) return;
        showLive(centeredAt(live ?? box, [proxy.position.x, proxy.position.y, proxy.position.z]));
    });

    const setRay = (ev: PointerEvent) => {
        const cam = getViewerRuntime().camera.current ?? camera;
        const rect = renderer.domElement.getBoundingClientRect();
        raycaster.setFromCamera(
            new THREE.Vector2(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1),
            cam,
        );
    };

    const onPointerDown = (ev: PointerEvent) => {
        if (ev.button !== 0) return;
        const box = activeBox();
        if (!box || !box.visible || !handles[0].visible) return;
        setRay(ev);
        const hit = raycaster.intersectObjects(handles, false)[0];
        if (!hit) return;
        const face = hit.object.userData.__bboxFace as number;
        const f = BOX_FACES[face];
        const c: Vec3 = faceCenter(box, face);
        const lineOrigin = new THREE.Vector3(c[0], c[1], c[2]);
        const lineDir = new THREE.Vector3(f.axis === 0 ? 1 : 0, f.axis === 1 ? 1 : 0, f.axis === 2 ? 1 : 0);
        const startT = lineParamFromRay(raycaster.ray, lineOrigin, lineDir);
        if (startT === null) return;
        // Ours, not the camera's: stop orbit controls (registered after us) from seeing it.
        ev.stopImmediatePropagation();
        ev.preventDefault();
        drag = {face, startBox: {min: [...box.min], max: [...box.max]}, lineOrigin, lineDir, startT, pointerId: ev.pointerId};
        swallowClick = true;
        setControlsEnabled(false);
        renderer.domElement.setPointerCapture(ev.pointerId);
    };

    const onPointerMove = (ev: PointerEvent) => {
        if (!drag || ev.pointerId !== drag.pointerId) return;
        setRay(ev);
        const t = lineParamFromRay(raycaster.ray, drag.lineOrigin, drag.lineDir);
        if (t === null) return;
        const s = boxSize(drag.startBox);
        const minSize = Math.max(1e-3, 0.001 * Math.max(s[0], s[1], s[2]));
        showLive(moveFace(drag.startBox, drag.face, t - drag.startT, minSize));
    };

    const onPointerUp = (ev: PointerEvent) => {
        if (!drag || ev.pointerId !== drag.pointerId) return;
        drag = null;
        if (renderer.domElement.hasPointerCapture(ev.pointerId)) renderer.domElement.releasePointerCapture(ev.pointerId);
        setControlsEnabled(true);
        if (live) commit(live);
    };

    const onClickCapture = (ev: MouseEvent) => {
        if (!swallowClick) return;
        swallowClick = false;
        ev.stopImmediatePropagation();
    };

    const dom = renderer.domElement;
    dom.addEventListener("pointerdown", onPointerDown, {capture: true});
    dom.addEventListener("pointermove", onPointerMove);
    dom.addEventListener("pointerup", onPointerUp);
    dom.addEventListener("pointercancel", onPointerUp);
    dom.addEventListener("click", onClickCapture, {capture: true});
    // A pointer press that never becomes a click (e.g. a drag ending off the canvas) must not
    // leave the next real click swallowed.
    const clearSwallow = () => {
        if (!drag && !gizmo.dragging) setTimeout(() => (swallowClick = false), 0);
    };
    window.addEventListener("pointerup", clearSwallow);

    rebuild();
    const unsub = useBBoxSelectionStore.subscribe((s, prev) => {
        rebuild();
        // The active box's selection follows its bounds and mode.
        const now = s.boxes.find((b) => b.id === s.activeId);
        const before = prev.boxes.find((b) => b.id === s.activeId);
        if (now && before && (now.mode !== before.mode || !sameVec(now.min, before.min) || !sameVec(now.max, before.max))) {
            applyBoxSelection(now.id);
        }
    });

    return () => {
        unsub();
        dom.removeEventListener("pointerdown", onPointerDown, {capture: true});
        dom.removeEventListener("pointermove", onPointerMove);
        dom.removeEventListener("pointerup", onPointerUp);
        dom.removeEventListener("pointercancel", onPointerUp);
        dom.removeEventListener("click", onClickCapture, {capture: true});
        window.removeEventListener("pointerup", clearSwallow);
        gizmo.detach();
        gizmo.dispose();
        scene.remove(gizmoHelper, proxy);
        for (const v of visuals.values()) disposeVisual(v);
        visuals.clear();
        for (const h of handles) (h.material as THREE.Material).dispose();
        handleGeometry.dispose();
        unitBox.dispose();
        unitEdges.dispose();
        scene.remove(container);
        boxPickTargets = [];
        requestRender();
    };
}

function sameVec(a: Vec3, b: Vec3): boolean {
    return a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
}

export default BBoxSelectionController;
