import React from "react";
import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";

import {cameraRef, controlsRef, rendererRef, sceneRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useModelState} from "@/state/modelState";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {
    applyFaceOffset,
    BOX_FACE_SIDES,
    edgeEndpoints,
    edgeHitOnFace,
    quantize,
    snapBox,
    type CellBox,
    type EdgeHit,
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
const HOVER_EDGE_COLOR = 0xfacc15;
const SELECTED_EDGE_COLOR = 0xfb7185;
const HOVER_EDGE_WIDTH = 4; // px (fat lines — WebGL ignores LineBasicMaterial.linewidth)
const SELECTED_EDGE_WIDTH = 6;
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

    // Cell meshes live in their own subgroup so "hide cells" toggles them
    // without touching the ghost or the builder grid.
    const cellsGroup = new THREE.Group();
    container.add(cellsGroup);

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

    // While a procedural model is open, the scene's static 1 m helper grid is
    // swapped for a builder grid whose line spacing IS the snap gridStep (and
    // which lives inside the container, so its intersections are exactly the
    // model-space points quantize() snaps to).
    let builderGrid: THREE.GridHelper | null = null;
    let builderGridStep = -1;
    const hiddenDefaultGrids: THREE.GridHelper[] = [];
    const GRID_TARGET_EXTENT = 60; // meters; divisions derive from gridStep
    const GRID_MAX_DIVISIONS = 2000;

    const disposeBuilderGrid = () => {
        if (!builderGrid) return;
        builderGrid.geometry.dispose();
        (builderGrid.material as THREE.Material).dispose();
        container.remove(builderGrid);
        builderGrid = null;
        builderGridStep = -1;
    };

    const syncBuilderGrid = () => {
        const st = useCellBuilderStore.getState();
        const wantGrid = st.active !== null && st.gridStep > 0;

        // Toggle the default scene grid(s) opposite to ours.
        if (wantGrid && hiddenDefaultGrids.length === 0) {
            for (const o of scene.children) {
                if (o instanceof THREE.GridHelper && o !== builderGrid && o.visible) {
                    o.visible = false;
                    hiddenDefaultGrids.push(o);
                }
            }
        } else if (!wantGrid && hiddenDefaultGrids.length > 0) {
            hiddenDefaultGrids.forEach((g) => (g.visible = true));
            hiddenDefaultGrids.length = 0;
        }

        if (!wantGrid) {
            disposeBuilderGrid();
            requestRender();
            return;
        }
        if (builderGrid && builderGridStep === st.gridStep) return;

        disposeBuilderGrid();
        // Even division count so the centered grid's lines land exactly on
        // n * gridStep (extent/2 must itself be a multiple of gridStep).
        const half = Math.min(GRID_MAX_DIVISIONS / 2, Math.max(1, Math.round(GRID_TARGET_EXTENT / (2 * st.gridStep))));
        const divisions = 2 * half;
        const extent = divisions * st.gridStep;
        builderGrid = new THREE.GridHelper(extent, divisions, 0x6b7280, 0x374151);
        (builderGrid.material as THREE.Material).depthWrite = false;
        builderGrid.renderOrder = -1;
        builderGrid.layers.set(1);
        if (useModelState.getState().zIsUp) {
            builderGrid.rotation.x = Math.PI / 2; // XZ default -> model XY plane
        }
        builderGridStep = st.gridStep;
        container.add(builderGrid);
        requestRender();
    };

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let drag: DragState | null = null;
    let hovered: {mesh: THREE.Mesh; faceIndex: number} | null = null;
    let hoveredEdge: {cellId: string; faceIndex: number; edge: EdgeHit} | null = null;

    const meshById = new Map<string, THREE.Mesh>();

    // Fat-line overlays for edge hover/selection (thickness in pixels; a plain
    // LineBasicMaterial's linewidth is ignored by WebGL).
    const makeEdgeOverlay = (color: number, linewidth: number): LineSegments2 => {
        const mat = new LineMaterial({color, linewidth, transparent: true, depthTest: false});
        const geo = new LineSegmentsGeometry();
        geo.setPositions([0, 0, 0, 0, 0, 0]);
        const line = new LineSegments2(geo, mat);
        line.visible = false;
        line.layers.set(1);
        container.add(line);
        return line;
    };
    const hoverEdgeLine = makeEdgeOverlay(HOVER_EDGE_COLOR, HOVER_EDGE_WIDTH);
    const selectedEdgeLine = makeEdgeOverlay(SELECTED_EDGE_COLOR, SELECTED_EDGE_WIDTH);

    const placeEdgeOverlay = (
        line: LineSegments2,
        cellId: string,
        faceIndex: number,
        edge: EdgeHit,
    ): boolean => {
        const cell = useCellBuilderStore.getState().cells[cellId];
        if (!cell) return false;
        const {start, end} = edgeEndpoints(cell, faceIndex, edge);
        line.geometry.dispose();
        const geo = new LineSegmentsGeometry();
        geo.setPositions([...start, ...end]);
        line.geometry = geo;
        const size = renderer.getSize(new THREE.Vector2());
        (line.material as LineMaterial).resolution.set(size.x, size.y);
        return true;
    };

    const refreshEdgeOverlays = () => {
        const st = useCellBuilderStore.getState();
        const sel = st.selection;
        selectedEdgeLine.visible =
            sel?.kind === "edge" && sel.faceIndex !== undefined && sel.edge !== undefined && st.cellsVisible
                ? placeEdgeOverlay(selectedEdgeLine, sel.cellId, sel.faceIndex, sel.edge)
                : false;
        const hoverIsSelected =
            hoveredEdge !== null &&
            sel?.kind === "edge" &&
            sel.cellId === hoveredEdge.cellId &&
            sel.faceIndex === hoveredEdge.faceIndex &&
            sel.edge?.axis === hoveredEdge.edge.axis &&
            sel.edge?.boundaryAxis === hoveredEdge.edge.boundaryAxis &&
            sel.edge?.boundaryPositive === hoveredEdge.edge.boundaryPositive;
        hoverEdgeLine.visible =
            hoveredEdge !== null && !hoverIsSelected && st.cellsVisible
                ? placeEdgeOverlay(hoverEdgeLine, hoveredEdge.cellId, hoveredEdge.faceIndex, hoveredEdge.edge)
                : false;
    };

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
        refreshEdgeOverlays();
        requestRender();
    };

    const rebuild = () => {
        for (let i = cellsGroup.children.length - 1; i >= 0; i--) {
            const o = cellsGroup.children[i];
            o.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(m);
            });
            cellsGroup.remove(o);
        }
        meshById.clear();
        hovered = null;
        hoveredEdge = null;

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
                cellsGroup.add(mesh);
                meshById.set(cell.id, mesh);
            }
        }
        cellsGroup.visible = st.cellsVisible;
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
        if (!cellsGroup.visible) return null; // hidden cells aren't pickable
        const hits = raycaster.intersectObjects([...meshById.values()], false);
        return hits.length ? hits[0] : null;
    };

    const syncCursor = () => {
        renderer.domElement.style.cursor = hoveredEdge ? "crosshair" : hovered ? "pointer" : "";
    };

    const setHoveredFace = (mesh: THREE.Mesh | null, faceIndex: number) => {
        const same = hovered?.mesh === mesh && hovered?.faceIndex === faceIndex;
        if (same || (!hovered && !mesh)) return;
        hovered = mesh ? {mesh, faceIndex} : null;
        syncCursor();
        refreshFaceStyles();
    };

    const sameEdge = (a: EdgeHit | null | undefined, b: EdgeHit | null | undefined): boolean =>
        !!a && !!b && a.axis === b.axis && a.boundaryAxis === b.boundaryAxis && a.boundaryPositive === b.boundaryPositive;

    const setHoveredEdge = (next: {cellId: string; faceIndex: number; edge: EdgeHit} | null) => {
        const same =
            (next === null && hoveredEdge === null) ||
            (next !== null &&
                hoveredEdge !== null &&
                next.cellId === hoveredEdge.cellId &&
                next.faceIndex === hoveredEdge.faceIndex &&
                sameEdge(next.edge, hoveredEdge.edge));
        if (same) return;
        hoveredEdge = next;
        syncCursor();
        refreshEdgeOverlays();
        requestRender();
    };

    // Shared edge tolerance: 8% of the face's smaller in-plane extent,
    // clamped to sane world-space bounds.
    const detectEdge = (cellId: string, faceIndex: number, hitPoint: THREE.Vector3): EdgeHit | null => {
        const cell = useCellBuilderStore.getState().cells[cellId];
        const side = BOX_FACE_SIDES[faceIndex];
        if (!cell || !side) return null;
        const inPlane = ([0, 1, 2] as const).filter((a) => a !== side.axis);
        const minExtent = Math.min(cell.size[inPlane[0]], cell.size[inPlane[1]]);
        const tol = Math.min(0.3, Math.max(0.06, minExtent * 0.08));
        return edgeHitOnFace(cell, faceIndex, worldToModel(hitPoint), tol);
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

        // "none" select mode = pure navigation: don't grab faces for
        // select/drag, let the camera controls handle the pointer.
        if (st.selectMode === "none") return;

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
        // "none" mode: a plain click selects nothing (free navigation).
        if (st.selectMode === "none") return;
        const cell = st.cells[drag_.cellId];
        if (!cell) return;

        setPointer(ev);
        const hit = pickBuilderMesh();

        // Border proximity -> edge selection (length-adjust panel), regardless
        // of the cell/face select mode.
        if (hit) {
            const edge = detectEdge(cell.id, drag_.faceIndex, hit.point);
            if (edge) {
                st.setSelection({kind: "edge", cellId: cell.id, faceIndex: drag_.faceIndex, edge});
                st.setPanelVisible(true);
                return;
            }
        }

        // The panel's select-mode toggle decides what a plain click picks.
        if (st.selectMode === "face") {
            st.setSelection({kind: "face", cellId: cell.id, faceIndex: drag_.faceIndex});
        } else {
            st.setSelection({kind: "cell", cellId: cell.id});
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
            // "none" mode: no hover highlights (free navigation).
            if (st.selectMode === "none") {
                setHoveredEdge(null);
                setHoveredFace(null, -1);
                return;
            }
            const hit = pickBuilderMesh();
            if (hit && hit.face) {
                const cellId = hit.object.userData.__cellId as string;
                const faceIndex = hit.face.materialIndex;
                const edge = detectEdge(cellId, faceIndex, hit.point);
                if (edge) {
                    // near a border: highlight the edge, not the face
                    setHoveredFace(null, -1);
                    setHoveredEdge({cellId, faceIndex, edge});
                } else {
                    setHoveredEdge(null);
                    setHoveredFace(hit.object as THREE.Mesh, faceIndex);
                }
            } else {
                setHoveredEdge(null);
                setHoveredFace(null, -1);
            }
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
    syncBuilderGrid();
    const unsub = useCellBuilderStore.subscribe((s, prev) => {
        if (s.cells !== prev.cells || s.active !== prev.active) rebuild();
        else if (s.selection !== prev.selection) refreshFaceStyles();
        if (s.active !== prev.active || s.gridStep !== prev.gridStep) syncBuilderGrid();
        if (s.cellsVisible !== prev.cellsVisible) {
            cellsGroup.visible = s.cellsVisible;
            refreshEdgeOverlays();
            requestRender();
        }
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
        hiddenDefaultGrids.forEach((g) => (g.visible = true));
        hiddenDefaultGrids.length = 0;
        disposeBuilderGrid();
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
