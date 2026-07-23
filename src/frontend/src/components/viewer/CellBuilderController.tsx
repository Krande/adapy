import React from "react";
import * as THREE from "three";

import {cameraRef, controlsRef, rendererRef, sceneRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useModelState} from "@/state/modelState";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {
    applyFaceOffset,
    BOX_FACE_SIDES,
    edgeHitOnFace,
    quantize,
    snapBox,
    type CellBox,
    type Vec3,
} from "@/utils/cellbuilder/snap";

// Headless controller for the procedural cellbuilder: reconciles the
// cellBuilderStore with tool-local three.js box meshes (blue = cell,
// orange = equipment), per-face hover highlight, click selection
// (cell -> face; border clicks select an edge), magnetic ghost placement in
// the add modes and grid-quantized face dragging. The container tracks the
// viewer's model translation so builder boxes align exactly with loaded
// GLBs (incl. the compiled result). Renders nothing.

const CELL_COLOR = 0x3b82f6;
const EQUIPMENT_COLOR = 0xf97316;
const GHOST_COLOR = 0x22c55e;
const HOVER_FACE_COLOR = 0xfacc15;
const SELECTED_FACE_COLOR = 0xfb7185;
const DEFAULT_CELL_SIZE: Vec3 = [5, 5, 3];
const DEFAULT_EQUIPMENT_SIZE: Vec3 = [1, 1, 1];
const BASE_OPACITY = 0.3;
const DRAG_START_PX = 4;

interface DragState {
    cellId: string;
    faceIndex: number;
    axis: 0 | 1 | 2;
    positiveFace: boolean;
    startBox: CellBox;
    // line through the face center along the face axis, world coords
    lineOrigin: THREE.Vector3;
    lineDir: THREE.Vector3;
    startT: number;
    startClientX: number;
    startClientY: number;
    started: boolean;
    pointerId: number;
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

    // Loaded GLBs are shifted by modelState.translation (bbox centering +
    // z-lift). The builder authors model-space coordinates, so the container
    // applies the same shift — cells and the compiled structure stay aligned.
    const syncOffset = () => {
        const t = useModelState.getState().translation;
        container.position.set(t?.x ?? 0, t?.y ?? 0, t?.z ?? 0);
        requestRender();
    };
    syncOffset();

    const offsetVec = (): THREE.Vector3 => container.position;
    const worldToModel = (p: THREE.Vector3): Vec3 => [
        p.x - container.position.x,
        p.y - container.position.y,
        p.z - container.position.z,
    ];

    const ghost = new THREE.Mesh(
        new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshBasicMaterial({color: GHOST_COLOR, transparent: true, opacity: 0.35, depthWrite: false}),
    );
    ghost.visible = false;
    container.add(ghost); // inherits the model offset
    let ghostBox: CellBox | null = null;

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let drag: DragState | null = null;
    let hovered: {mesh: THREE.Mesh; faceIndex: number} | null = null;

    const meshById = new Map<string, THREE.Mesh>();

    const disposeMesh = (m: THREE.Mesh) => {
        m.geometry.dispose();
        const mats = Array.isArray(m.material) ? m.material : [m.material];
        mats.forEach((x) => x.dispose());
    };

    // Recompute every face material's color/opacity from base + selection +
    // hover state. Cheap (6 materials per box) and keeps one source of truth.
    const refreshFaceStyles = () => {
        const st = useCellBuilderStore.getState();
        const sel = st.selection;
        for (const [cellId, mesh] of meshById) {
            const cell = st.cells[cellId];
            if (!cell) continue;
            const base = cell.kind === "cell" ? CELL_COLOR : EQUIPMENT_COLOR;
            const cellSelected = sel?.cellId === cellId;
            const mats = mesh.material as THREE.MeshBasicMaterial[];
            for (let fi = 0; fi < mats.length; fi++) {
                let color = base;
                let opacity = BASE_OPACITY;
                if (cellSelected) opacity = 0.4;
                if (cellSelected && sel?.kind === "face" && sel.faceIndex === fi) {
                    color = SELECTED_FACE_COLOR;
                    opacity = 0.55;
                }
                if (hovered?.mesh === mesh && hovered.faceIndex === fi) {
                    color = HOVER_FACE_COLOR;
                    opacity = 0.6;
                }
                mats[fi].color.setHex(color);
                mats[fi].opacity = opacity;
            }
            const edgeLines = mesh.children[0] as THREE.LineSegments | undefined;
            if (edgeLines) {
                (edgeLines.material as THREE.LineBasicMaterial).color.setHex(cellSelected ? 0xffffff : base);
            }
        }
        requestRender();
    };

    const rebuild = () => {
        for (let i = container.children.length - 1; i >= 0; i--) {
            const o = container.children[i];
            if (o === ghost) continue;
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
                // One material per BoxGeometry group (+X,-X,+Y,-Y,+Z,-Z) so a
                // single face can highlight on hover/selection.
                const mats = BOX_FACE_SIDES.map(
                    () =>
                        new THREE.MeshBasicMaterial({
                            color,
                            transparent: true,
                            opacity: BASE_OPACITY,
                            depthWrite: false,
                        }),
                );
                const mesh = new THREE.Mesh(geo, mats);
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
        refreshFaceStyles();
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

    const setHoveredFace = (mesh: THREE.Mesh | null, faceIndex: number) => {
        const same = hovered?.mesh === mesh && hovered?.faceIndex === faceIndex;
        if (same || (!hovered && !mesh)) return;
        hovered = mesh ? {mesh, faceIndex} : null;
        renderer.domElement.style.cursor = hovered ? "pointer" : "";
        refreshFaceStyles();
    };

    const updateGhost = () => {
        const st = useCellBuilderStore.getState();
        const size = st.mode === "add-cell" ? DEFAULT_CELL_SIZE : DEFAULT_EQUIPMENT_SIZE;
        // Place on top of a hovered cell, else on the model's ground plane.
        const hit = pickBuilderMesh();
        let base: Vec3 | null = null;
        let z = 0;
        if (hit) {
            const cellId = hit.object.userData.__cellId as string;
            const cell = st.cells[cellId];
            base = worldToModel(hit.point);
            z = cell ? cell.origin[2] + cell.size[2] : base[2];
        } else {
            const groundPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -offsetVec().z);
            const w = raycaster.ray.intersectPlane(groundPlane, new THREE.Vector3());
            if (w) base = worldToModel(w);
        }
        if (!base) {
            ghost.visible = false;
            ghostBox = null;
            return;
        }
        let box: CellBox = {
            origin: [
                quantize(base[0] - size[0] / 2, st.gridStep),
                quantize(base[1] - size[1] / 2, st.gridStep),
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

        const faceIndex = hit.face.materialIndex;
        const side = BOX_FACE_SIDES[faceIndex];
        if (!side) return;

        const center = new THREE.Vector3(
            cell.origin[0] + cell.size[0] / 2,
            cell.origin[1] + cell.size[1] / 2,
            cell.origin[2] + cell.size[2] / 2,
        ).add(offsetVec());
        const lineDir = new THREE.Vector3(side.axis === 0 ? 1 : 0, side.axis === 1 ? 1 : 0, side.axis === 2 ? 1 : 0);
        const startT = lineParamFromRay(raycaster.ray, center, lineDir);
        if (startT === null) return;

        // Pending drag: becomes a real face-drag only after DRAG_START_PX of
        // pointer travel — otherwise pointerup treats it as a selection click.
        drag = {
            cellId,
            faceIndex,
            axis: side.axis,
            positiveFace: side.positive,
            startBox: {origin: [...cell.origin], size: [...cell.size]},
            lineOrigin: center,
            lineDir,
            startT,
            startClientX: ev.clientX,
            startClientY: ev.clientY,
            started: false,
            pointerId: ev.pointerId,
        };
        ev.stopPropagation();
    };

    const resolveClickSelection = (drag_: DragState, ev: PointerEvent) => {
        const st = useCellBuilderStore.getState();
        const cell = st.cells[drag_.cellId];
        if (!cell) return;

        setPointer(ev);
        const hit = pickBuilderMesh();
        const side = BOX_FACE_SIDES[drag_.faceIndex];

        // Border proximity -> edge selection (length-adjust panel). Tolerance
        // scales with the face's in-plane extent, clamped to sane bounds.
        if (hit && side) {
            const inPlane = ([0, 1, 2] as const).filter((a) => a !== side.axis);
            const minExtent = Math.min(cell.size[inPlane[0]], cell.size[inPlane[1]]);
            const tol = Math.min(0.3, Math.max(0.06, minExtent * 0.08));
            const edge = edgeHitOnFace(cell, drag_.faceIndex, worldToModel(hit.point), tol);
            if (edge) {
                st.setSelection({kind: "edge", cellId: cell.id, edgeAxis: edge.axis});
                st.setPanelVisible(true);
                return;
            }
        }

        // Cell-first selection: first click picks the cell; a click on an
        // already-selected cell picks the face under the cursor.
        if (st.selection?.cellId !== cell.id) {
            st.setSelection({kind: "cell", cellId: cell.id});
        } else {
            st.setSelection({kind: "face", cellId: cell.id, faceIndex: drag_.faceIndex});
        }
        st.setPanelVisible(true);
    };

    const onPointerMove = (ev: PointerEvent) => {
        const st = useCellBuilderStore.getState();
        if (!st.active) return;
        setPointer(ev);

        if (drag) {
            if (!drag.started) {
                const dx = ev.clientX - drag.startClientX;
                const dy = ev.clientY - drag.startClientY;
                if (Math.hypot(dx, dy) < DRAG_START_PX) return;
                drag.started = true;
                st.setMode("drag-face");
                if (controlsRef.current) controlsRef.current.enabled = false;
                renderer.domElement.setPointerCapture(drag.pointerId);
            }
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
            if (hit && hit.face) setHoveredFace(hit.object as THREE.Mesh, hit.face.materialIndex);
            else setHoveredFace(null, -1);
        }
    };

    const onPointerUp = (ev: PointerEvent) => {
        if (!drag) return;
        const wasDrag = drag.started;
        const pending = drag;
        drag = null;
        if (wasDrag) {
            useCellBuilderStore.getState().setMode("idle");
            if (controlsRef.current) controlsRef.current.enabled = true;
            try {
                renderer.domElement.releasePointerCapture(pending.pointerId);
            } catch {
                /* already released */
            }
        } else {
            resolveClickSelection(pending, ev);
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
        } else if (st.selection) {
            st.setSelection(null);
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
        else if (s.selection !== prev.selection) refreshFaceStyles();
        if (s.mode !== prev.mode && s.mode !== "add-cell" && s.mode !== "add-equipment") {
            ghost.visible = false;
            ghostBox = null;
            requestRender();
        }
    });
    const unsubModel = useModelState.subscribe((s, prev) => {
        if (s.translation !== prev.translation) syncOffset();
    });

    return () => {
        unsub();
        unsubModel();
        el.removeEventListener("pointerdown", onPointerDown, true);
        el.removeEventListener("pointermove", onPointerMove, true);
        el.removeEventListener("pointerup", onPointerUp, true);
        window.removeEventListener("keydown", onKeyDown);
        if (controlsRef.current) controlsRef.current.enabled = true;
        for (let i = container.children.length - 1; i >= 0; i--) {
            const o = container.children[i];
            o.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(m);
            });
            container.remove(o);
        }
        scene.remove(container);
        requestRender();
    };
}

export default CellBuilderController;
