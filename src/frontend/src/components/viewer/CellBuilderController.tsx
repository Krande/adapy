import React from "react";
import * as THREE from "three";

import {cameraRef, controlsRef, rendererRef, sceneRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {applyFaceOffset, quantize, snapBox, type CellBox, type Vec3} from "@/utils/cellbuilder/snap";

// Headless controller for the procedural cellbuilder: reconciles the
// cellBuilderStore with tool-local three.js box meshes (blue = cell,
// orange = equipment), handles magnetic ghost placement in the add modes and
// grid-quantized face dragging (extend/contract). Renders nothing.

const CELL_COLOR = 0x3b82f6;
const EQUIPMENT_COLOR = 0xf97316;
const GHOST_COLOR = 0x22c55e;
const DEFAULT_CELL_SIZE: Vec3 = [5, 5, 3];
const DEFAULT_EQUIPMENT_SIZE: Vec3 = [1, 1, 1];

interface DragState {
    cellId: string;
    axis: 0 | 1 | 2;
    positiveFace: boolean;
    startBox: CellBox;
    // line through the face center along the face axis, world coords
    lineOrigin: THREE.Vector3;
    lineDir: THREE.Vector3;
    startT: number;
}

const CellBuilderController: React.FC = () => {
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

function lineParamFromRay(ray: THREE.Ray, lineOrigin: THREE.Vector3, lineDir: THREE.Vector3): number | null {
    // Closest-point parameter along the (unit) line for the pointer ray.
    const w0 = new THREE.Vector3().subVectors(ray.origin, lineOrigin);
    const b = ray.direction.dot(lineDir);
    const denom = 1 - b * b;
    if (Math.abs(denom) < 1e-6) return null; // ray ~parallel to the drag axis
    const d = ray.direction.dot(w0);
    const e = lineDir.dot(w0);
    return (e - b * d) / denom;
}

function init(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera): () => void {
    const container = new THREE.Group();
    container.name = "__cellbuilder__";
    container.userData.__excludeFromFit = true;
    scene.add(container);

    const ghost = new THREE.Mesh(
        new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshBasicMaterial({color: GHOST_COLOR, transparent: true, opacity: 0.35, depthWrite: false}),
    );
    ghost.visible = false;
    ghost.userData.__excludeFromFit = true;
    scene.add(ghost);
    let ghostBox: CellBox | null = null;

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const groundPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
    let drag: DragState | null = null;
    let hovered: THREE.Mesh | null = null;

    const meshById = new Map<string, THREE.Mesh>();

    const disposeMesh = (m: THREE.Mesh) => {
        m.geometry.dispose();
        const mats = Array.isArray(m.material) ? m.material : [m.material];
        mats.forEach((x) => x.dispose());
    };

    const rebuild = () => {
        for (let i = container.children.length - 1; i >= 0; i--) {
            const o = container.children[i];
            o.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(m);
            });
            container.remove(o);
        }
        meshById.clear();
        hovered = null;

        const st = useCellBuilderStore.getState();
        if (st.active) {
            for (const cell of Object.values(st.cells)) {
                const geo = new THREE.BoxGeometry(...cell.size);
                const color = cell.kind === "cell" ? CELL_COLOR : EQUIPMENT_COLOR;
                const mesh = new THREE.Mesh(
                    geo,
                    new THREE.MeshBasicMaterial({color, transparent: true, opacity: 0.3, depthWrite: false}),
                );
                mesh.position.set(
                    cell.origin[0] + cell.size[0] / 2,
                    cell.origin[1] + cell.size[1] / 2,
                    cell.origin[2] + cell.size[2] / 2,
                );
                mesh.userData.__cellId = cell.id;
                const edges = new THREE.LineSegments(
                    new THREE.EdgesGeometry(geo),
                    new THREE.LineBasicMaterial({color}),
                );
                mesh.add(edges);
                container.add(mesh);
                meshById.set(cell.id, mesh);
            }
        }
        ghost.visible = false;
        ghostBox = null;
        requestRender();
    };

    const setPointer = (ev: PointerEvent) => {
        const rect = renderer.domElement.getBoundingClientRect();
        pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
        pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(pointer, cameraRef.current ?? (camera as any));
    };

    const pickBuilderMesh = (): THREE.Intersection | null => {
        const hits = raycaster.intersectObjects([...meshById.values()], false);
        return hits.length ? hits[0] : null;
    };

    const setHovered = (mesh: THREE.Mesh | null) => {
        if (hovered === mesh) return;
        if (hovered) (hovered.material as THREE.MeshBasicMaterial).opacity = 0.3;
        hovered = mesh;
        if (hovered) (hovered.material as THREE.MeshBasicMaterial).opacity = 0.55;
        renderer.domElement.style.cursor = hovered ? "ew-resize" : "";
        requestRender();
    };

    const updateGhost = () => {
        const st = useCellBuilderStore.getState();
        const size = st.mode === "add-cell" ? DEFAULT_CELL_SIZE : DEFAULT_EQUIPMENT_SIZE;
        // Place on top of a hovered cell, else on the ground plane.
        const hit = pickBuilderMesh();
        let base: THREE.Vector3 | null = null;
        let z = 0;
        if (hit) {
            const cellId = hit.object.userData.__cellId as string;
            const cell = st.cells[cellId];
            base = hit.point.clone();
            z = cell ? cell.origin[2] + cell.size[2] : hit.point.z;
        } else {
            base = raycaster.ray.intersectPlane(groundPlane, new THREE.Vector3());
        }
        if (!base) {
            ghost.visible = false;
            ghostBox = null;
            return;
        }
        let box: CellBox = {
            origin: [
                quantize(base.x - size[0] / 2, st.gridStep),
                quantize(base.y - size[1] / 2, st.gridStep),
                quantize(z, st.gridStep),
            ],
            size,
        };
        box = snapBox(box, Object.values(st.cells), st.snapThreshold);
        ghostBox = box;
        ghost.scale.set(...box.size);
        ghost.position.set(
            box.origin[0] + box.size[0] / 2,
            box.origin[1] + box.size[1] / 2,
            box.origin[2] + box.size[2] / 2,
        );
        ghost.visible = true;
        requestRender();
    };

    const onPointerDown = (ev: PointerEvent) => {
        const st = useCellBuilderStore.getState();
        if (!st.active || ev.button !== 0) return;
        setPointer(ev);

        if (st.mode === "add-cell" || st.mode === "add-equipment") {
            updateGhost();
            if (ghostBox) {
                st.addCell(st.mode === "add-cell" ? "cell" : "equipment", ghostBox.origin, ghostBox.size);
            }
            ev.stopPropagation();
            return;
        }

        const hit = pickBuilderMesh();
        if (!hit || !hit.face) return;
        const cellId = hit.object.userData.__cellId as string;
        const cell = st.cells[cellId];
        if (!cell) return;

        // Boxes are axis-aligned and unrotated: the local face normal IS the
        // world normal; its dominant component identifies the dragged face.
        const n = hit.face.normal;
        const axis = (Math.abs(n.x) > 0.5 ? 0 : Math.abs(n.y) > 0.5 ? 1 : 2) as 0 | 1 | 2;
        const positiveFace = [n.x, n.y, n.z][axis] > 0;

        const center = new THREE.Vector3(
            cell.origin[0] + cell.size[0] / 2,
            cell.origin[1] + cell.size[1] / 2,
            cell.origin[2] + cell.size[2] / 2,
        );
        const lineDir = new THREE.Vector3(axis === 0 ? 1 : 0, axis === 1 ? 1 : 0, axis === 2 ? 1 : 0);
        const startT = lineParamFromRay(raycaster.ray, center, lineDir);
        if (startT === null) return;

        drag = {
            cellId,
            axis,
            positiveFace,
            startBox: {origin: [...cell.origin], size: [...cell.size]},
            lineOrigin: center,
            lineDir,
            startT,
        };
        st.setMode("drag-face");
        if (controlsRef.current) controlsRef.current.enabled = false;
        renderer.domElement.setPointerCapture(ev.pointerId);
        ev.stopPropagation();
    };

    const onPointerMove = (ev: PointerEvent) => {
        const st = useCellBuilderStore.getState();
        if (!st.active) return;
        setPointer(ev);

        if (drag) {
            const t = lineParamFromRay(raycaster.ray, drag.lineOrigin, drag.lineDir);
            if (t === null) return;
            // signed face displacement along +axis; applyFaceOffset knows which
            // face moves (positive face scales size, negative face shifts origin)
            const offset = quantize(t - drag.startT, st.gridStep);
            const next = applyFaceOffset(drag.startBox, drag.axis, drag.positiveFace, offset, st.gridStep || 0.1);
            st.updateCell(drag.cellId, {origin: next.origin, size: next.size});
            ev.stopPropagation();
            return;
        }

        if (st.mode === "add-cell" || st.mode === "add-equipment") {
            updateGhost();
            return;
        }

        if (st.mode === "idle") {
            const hit = pickBuilderMesh();
            setHovered(hit ? (hit.object as THREE.Mesh) : null);
        }
    };

    const onPointerUp = (ev: PointerEvent) => {
        if (!drag) return;
        drag = null;
        useCellBuilderStore.getState().setMode("idle");
        if (controlsRef.current) controlsRef.current.enabled = true;
        try {
            renderer.domElement.releasePointerCapture(ev.pointerId);
        } catch {
            /* already released */
        }
        ev.stopPropagation();
    };

    const onKeyDown = (ev: KeyboardEvent) => {
        if (ev.key !== "Escape") return;
        const st = useCellBuilderStore.getState();
        if (st.mode !== "idle") {
            st.setMode("idle");
            ghost.visible = false;
            requestRender();
        }
    };

    const el = renderer.domElement;
    // Capture phase so a grab on a builder face wins over the scene's own
    // click-selection/orbit-pivot handlers.
    el.addEventListener("pointerdown", onPointerDown, true);
    el.addEventListener("pointermove", onPointerMove, true);
    el.addEventListener("pointerup", onPointerUp, true);
    window.addEventListener("keydown", onKeyDown);

    rebuild();
    const unsub = useCellBuilderStore.subscribe((s, prev) => {
        if (s.cells !== prev.cells || s.active !== prev.active) rebuild();
        if (s.mode !== prev.mode && s.mode !== "add-cell" && s.mode !== "add-equipment") {
            ghost.visible = false;
            ghostBox = null;
            requestRender();
        }
    });

    return () => {
        unsub();
        el.removeEventListener("pointerdown", onPointerDown, true);
        el.removeEventListener("pointermove", onPointerMove, true);
        el.removeEventListener("pointerup", onPointerUp, true);
        window.removeEventListener("keydown", onKeyDown);
        if (controlsRef.current) controlsRef.current.enabled = true;
        rebuildCleanup();
        requestRender();
    };

    function rebuildCleanup() {
        for (let i = container.children.length - 1; i >= 0; i--) {
            const o = container.children[i];
            o.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(m);
            });
            container.remove(o);
        }
        scene.remove(container);
        disposeMesh(ghost);
        scene.remove(ghost);
    }
}

export default CellBuilderController;
