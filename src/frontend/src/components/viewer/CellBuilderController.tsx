import React from "react";
import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";

import {cameraRef, controlsRef, rendererRef, sceneRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useModelState} from "@/state/modelState";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import type {ProceduralTypeOption, TypePortSummary} from "@/services/viewerApi";
import {portColorInt} from "@/utils/portColor";
import {
    applyFaceOffset,
    BOX_FACE_SIDES,
    edgeEndpoints,
    edgeHitOnFace,
    faceCenter,
    originFromCenter,
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
// Resize-handle sphere colour per axis (X red, Y green, Z blue).
const HANDLE_AXIS_COLOR = [0xef4444, 0x22c55e, 0x3b82f6];
const LONG_PRESS_MS = 500;
const LONG_PRESS_MOVE_PX = 10;

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

// Ports for a placed equipment cell, looked up from the fetched type options.
// The store's link is loose — cell.equipmentType is a display name string (the
// entity DESCRIPTION), not a hard slug — so reconcile by slug first, then by
// name (case-insensitive). Cells that aren't equipment, or types without port
// geometry, yield [].
function portsForEquipment(cell: BuilderCell, types: ProceduralTypeOption[]): TypePortSummary[] {
    if (cell.kind !== "equipment" || !cell.equipmentType) return [];
    const key = cell.equipmentType.toLowerCase();
    const t =
        types.find((o) => o.slug.toLowerCase() === key) ?? types.find((o) => o.name.toLowerCase() === key);
    return t?.ports ?? [];
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

    // Port/nozzle overlay: coloured arrows at each placed equipment's I/O
    // positions/vectors. Its own subgroup (toggled independently of the cells)
    // and inherits the container's model offset so glyphs align with the
    // compiled structure.
    const portsGroup = new THREE.Group();
    container.add(portsGroup);

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

    // ArrowHelper owns a Line (non-LineSegments) + a Mesh; the generic
    // container-dispose loop only frees meshes/line-segments, so free both parts
    // explicitly here.
    const disposeArrow = (a: THREE.ArrowHelper) => {
        a.line.geometry.dispose();
        (a.line.material as THREE.Material).dispose();
        a.cone.geometry.dispose();
        (a.cone.material as THREE.Material).dispose();
    };

    const clearPorts = () => {
        for (let i = portsGroup.children.length - 1; i >= 0; i--) {
            const o = portsGroup.children[i];
            if (o instanceof THREE.ArrowHelper) disposeArrow(o);
            portsGroup.remove(o);
        }
    };

    // Redraw the port overlay from the placed equipment cells. Each port becomes
    // a coloured arrow at (cell centre in x/y, cell base in z) + local position,
    // matching the compiler's equipment origin (X+LX/2, Y+LY/2, Z) and the
    // catalog preview's arrow colours. Arrows sit on layer 1 so they never
    // intercept picks.
    const rebuildPorts = () => {
        clearPorts();
        const st = useCellBuilderStore.getState();
        portsGroup.visible = st.portsOverlayVisible;
        if (!st.active || !st.portsOverlayVisible) {
            requestRender();
            return;
        }
        for (const cell of Object.values(st.cells)) {
            const ports = portsForEquipment(cell, st.equipmentTypes);
            if (!ports.length) continue;
            const len = Math.max(0.2, 0.3 * Math.max(cell.size[0], cell.size[1], cell.size[2]));
            const cx = cell.origin[0] + cell.size[0] / 2;
            const cy = cell.origin[1] + cell.size[1] / 2;
            const cz = cell.origin[2];
            for (const p of ports) {
                const pos = p.position ?? [0, 0, 0];
                const dv = p.direction_vector ?? [0, 0, 1];
                const origin = new THREE.Vector3(cx + pos[0], cy + pos[1], cz + pos[2]);
                const dir = new THREE.Vector3(dv[0], dv[1], dv[2]);
                if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
                dir.normalize();
                // direction_vector is the outward nozzle normal; draw actual
                // flow — INPUT points into the equipment, OUTPUT points out,
                // INOUT stays outward.
                if (p.direction === "IN") dir.negate();
                const arrow = new THREE.ArrowHelper(dir, origin, len, portColorInt(p), len * 0.4, len * 0.25);
                arrow.traverse((o) => o.layers.set(1));
                portsGroup.add(arrow);
            }
        }
        requestRender();
    };

    // --- Direct-manipulation gizmos -------------------------------------
    // Translate: a THREE TransformControls widget drives a proxy whose
    // model-space position maps back to the selected cell's centre. The proxy
    // lives in the container so it shares the model offset. Resize: six
    // touch-friendly spheres at the cell's face centres, each dragged with the
    // same applyFaceOffset math as a face drag.
    const gizmoProxy = new THREE.Object3D();
    gizmoProxy.userData.__excludeFromFit = true;
    container.add(gizmoProxy);

    const gizmo = new TransformControls(cameraRef.current ?? (camera as THREE.Camera), renderer.domElement);
    gizmo.setSpace("world");
    const gizmoHelper = gizmo.getHelper();
    gizmoHelper.userData.__excludeFromFit = true;
    gizmoHelper.visible = false;
    scene.add(gizmoHelper);

    gizmo.addEventListener("dragging-changed", (e: any) => {
        const st = useCellBuilderStore.getState();
        if (controlsRef.current) controlsRef.current.enabled = !e.value;
        // Coalesce the whole widget drag into one undo step.
        if (e.value) st.beginTransaction();
        else st.endTransaction();
        requestRender();
    });
    gizmo.addEventListener("objectChange", () => {
        const st = useCellBuilderStore.getState();
        const sel = st.selection;
        if (st.gizmoMode !== "translate" || !sel) return;
        const cell = st.cells[sel.cellId];
        if (!cell) return;
        const step = st.gridStep > 0 ? st.gridStep : 0.1;
        const origin = originFromCenter(
            [gizmoProxy.position.x, gizmoProxy.position.y, gizmoProxy.position.z],
            cell.size,
            step,
        );
        st.updateCell(cell.id, {origin});
    });

    const resizeGroup = new THREE.Group();
    resizeGroup.visible = false;
    container.add(resizeGroup);

    const disposeResizeHandles = () => {
        for (let i = resizeGroup.children.length - 1; i >= 0; i--) {
            const o = resizeGroup.children[i] as THREE.Mesh;
            o.geometry.dispose();
            (o.material as THREE.Material).dispose();
            resizeGroup.remove(o);
        }
    };

    const rebuildResizeHandles = () => {
        disposeResizeHandles();
        const st = useCellBuilderStore.getState();
        const sel = st.selection;
        const cell = sel ? st.cells[sel.cellId] : null;
        const show = !!(st.active && st.gizmoMode === "resize" && cell && st.cellsVisible);
        resizeGroup.visible = show;
        if (!show || !cell) return;
        const r = Math.max(0.15, 0.1 * Math.min(cell.size[0], cell.size[1], cell.size[2]));
        for (let fi = 0; fi < BOX_FACE_SIDES.length; fi++) {
            const side = BOX_FACE_SIDES[fi];
            const c = faceCenter(cell, fi);
            const mesh = new THREE.Mesh(
                new THREE.SphereGeometry(r, 16, 12),
                new THREE.MeshBasicMaterial({
                    color: HANDLE_AXIS_COLOR[side.axis],
                    depthTest: false,
                    transparent: true,
                    opacity: 0.9,
                }),
            );
            mesh.position.set(c[0], c[1], c[2]);
            mesh.renderOrder = 3;
            mesh.userData.__resizeFace = fi;
            mesh.userData.__cellId = cell.id;
            resizeGroup.add(mesh);
        }
    };

    // Reconcile the gizmos with the current selection/mode. Skipped repositioning
    // of the translate proxy mid-drag so it never fights the pointer.
    const syncGizmo = () => {
        const st = useCellBuilderStore.getState();
        if (cameraRef.current) gizmo.camera = cameraRef.current;
        const sel = st.selection;
        const cell = sel ? st.cells[sel.cellId] : null;
        const translateOn = !!(st.active && st.gizmoMode === "translate" && cell && st.cellsVisible);
        if (translateOn && cell) {
            gizmo.setTranslationSnap(st.gridStep > 0 ? st.gridStep : null);
            if (!gizmo.dragging) {
                gizmoProxy.position.set(
                    cell.origin[0] + cell.size[0] / 2,
                    cell.origin[1] + cell.size[1] / 2,
                    cell.origin[2] + cell.size[2] / 2,
                );
            }
            if (gizmo.object !== gizmoProxy) gizmo.attach(gizmoProxy);
            gizmo.setMode("translate");
            gizmoHelper.visible = true;
        } else {
            if (gizmo.object) gizmo.detach();
            gizmoHelper.visible = false;
        }
        rebuildResizeHandles();
        requestRender();
    };

    // Begin a face drag (positive face scales size; negative face shifts origin).
    // ``immediate`` starts it right away (resize-handle grab) vs. after
    // DRAG_START_PX of travel (a face press, once face-drag resizing is on).
    const startFaceDrag = (cell: BuilderCell, faceIndex: number, ev: PointerEvent, immediate: boolean): boolean => {
        const side = BOX_FACE_SIDES[faceIndex];
        if (!side) return false;
        const center = new THREE.Vector3(
            cell.origin[0] + cell.size[0] / 2,
            cell.origin[1] + cell.size[1] / 2,
            cell.origin[2] + cell.size[2] / 2,
        ).add(offsetVec());
        const lineDir = new THREE.Vector3(side.axis === 0 ? 1 : 0, side.axis === 1 ? 1 : 0, side.axis === 2 ? 1 : 0);
        const startT = lineParamFromRay(raycaster.ray, center, lineDir);
        if (startT === null) return false;
        drag = {
            cellId: cell.id,
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
        if (immediate) {
            const st = useCellBuilderStore.getState();
            drag.started = true;
            st.setMode("drag-face");
            st.beginTransaction();
            if (controlsRef.current) controlsRef.current.enabled = false;
            renderer.domElement.setPointerCapture(ev.pointerId);
        }
        return true;
    };

    // A tap on empty space while a gizmo is active exits it. Recorded on
    // pointerdown, resolved on pointerup (a drag past DRAG_START_PX = orbit,
    // not an exit).
    let pendingGizmoExit: {x: number; y: number} | null = null;

    // --- Long-press → cell context menu (touch) --------------------------
    let longPressTimer: ReturnType<typeof setTimeout> | null = null;
    let longPressStartX = 0;
    let longPressStartY = 0;

    const clearLongPress = () => {
        if (longPressTimer !== null) {
            clearTimeout(longPressTimer);
            longPressTimer = null;
        }
    };

    const armLongPress = (ev: PointerEvent) => {
        clearLongPress();
        if (ev.pointerType !== "touch") return;
        const st = useCellBuilderStore.getState();
        if (!st.active) return;
        // A press on the translate gizmo's handle is a drag, not a long-press.
        if (st.gizmoMode === "translate" && gizmo.axis) return;
        const hit = pickBuilderMesh();
        if (!hit) return;
        const cellId = hit.object.userData.__cellId as string;
        longPressStartX = ev.clientX;
        longPressStartY = ev.clientY;
        const {clientX, clientY} = ev;
        longPressTimer = setTimeout(() => {
            longPressTimer = null;
            // A long-press wins over a pending face-drag/selection.
            drag = null;
            useCellBuilderStore.getState().openContextMenu(clientX, clientY, cellId);
        }, LONG_PRESS_MS);
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

        // Touch long-press → cell context menu (armed here, fires on hold).
        armLongPress(ev);

        // A gizmo owns the cell: manipulate its handles, and a tap on empty
        // space exits the gizmo (a drag on empty space still orbits).
        if (st.gizmoMode !== "none") {
            if (st.gizmoMode === "resize") {
                const hits = raycaster.intersectObjects(resizeGroup.children, false);
                if (hits.length) {
                    const fi = hits[0].object.userData.__resizeFace as number;
                    const cellId = hits[0].object.userData.__cellId as string;
                    const cell = st.cells[cellId];
                    if (cell && startFaceDrag(cell, fi, ev, true)) {
                        clearLongPress();
                        ev.stopPropagation();
                    }
                    return;
                }
            }
            // Translate: TransformControls owns the pointer over its handles.
            if (st.gizmoMode === "translate" && gizmo.axis) return;
            // Missed the handles: over a cell body do nothing (let it orbit);
            // over empty space, arm an exit resolved as a tap on pointerup.
            if (!pickBuilderMesh()) {
                pendingGizmoExit = {x: ev.clientX, y: ev.clientY};
            }
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

        // Pending drag: becomes a real face-drag only after DRAG_START_PX of
        // pointer travel (and only when face-drag resizing is enabled) —
        // otherwise pointerup treats it as a selection click.
        if (!startFaceDrag(cell, hit.face.materialIndex, ev, false)) return;
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
                // setSelection surfaces the details in the Selected Object Info
                // panel (see cellBuilderStore) — no need to open the tool panel.
                st.setSelection({kind: "edge", cellId: cell.id, faceIndex: drag_.faceIndex, edge});
                return;
            }
        }

        // The panel's select-mode toggle decides what a plain click picks.
        if (st.selectMode === "face") {
            st.setSelection({kind: "face", cellId: cell.id, faceIndex: drag_.faceIndex});
        } else {
            st.setSelection({kind: "cell", cellId: cell.id});
        }
    };

    const onPointerMove = (ev: PointerEvent) => {
        const st = useCellBuilderStore.getState();
        if (!st.active) return;

        // A moving finger cancels a pending long-press (it's a drag, not a hold).
        if (longPressTimer !== null &&
            Math.hypot(ev.clientX - longPressStartX, ev.clientY - longPressStartY) > LONG_PRESS_MOVE_PX) {
            clearLongPress();
        }
        // A drag on empty space is an orbit, not a gizmo-exit tap.
        if (pendingGizmoExit &&
            Math.hypot(ev.clientX - pendingGizmoExit.x, ev.clientY - pendingGizmoExit.y) > DRAG_START_PX) {
            pendingGizmoExit = null;
        }

        setPointer(ev);

        if (drag) {
            if (!drag.started) {
                const dx = ev.clientX - drag.startClientX;
                const dy = ev.clientY - drag.startClientY;
                if (Math.hypot(dx, dy) < DRAG_START_PX) return;
                // Face-drag resizing is opt-in: without it, dragging a face does
                // nothing (resizing goes through the explicit resize gizmo). Drop
                // the pending drag so it's neither a resize nor a stray select.
                if (!st.faceDragResize) {
                    drag = null;
                    return;
                }
                drag.started = true;
                st.setMode("drag-face");
                // Coalesce the whole drag into one undo step.
                st.beginTransaction();
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
            // No face/edge hover highlights while a gizmo owns the cell, or in
            // "none" mode (free navigation).
            if (st.gizmoMode !== "none" || st.selectMode === "none") {
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

    // End a (pending or active) face-drag. Always restores the camera controls
    // and releases the pointer capture the drag grabbed — critically also on
    // ``pointercancel``, which touch devices fire routinely when the browser's
    // gesture recogniser takes over (scroll/pinch) or a second finger lands. If
    // only ``pointerup`` restored them, a cancelled touch would leave
    // ``controls.enabled = false`` forever and the camera would appear broken.
    const finalizeDrag = (ev: PointerEvent, cancelled: boolean) => {
        if (!drag) return;
        const wasDrag = drag.started;
        const pending = drag;
        drag = null;
        if (wasDrag) {
            const st = useCellBuilderStore.getState();
            st.setMode("idle");
            st.endTransaction(); // close the coalesced-drag undo step
            if (controlsRef.current) controlsRef.current.enabled = true;
            try {
                renderer.domElement.releasePointerCapture(pending.pointerId);
            } catch {
                /* already released */
            }
        } else if (!cancelled) {
            // A pending-but-never-dragged pointerup is a selection click; a
            // cancelled gesture selects nothing.
            resolveClickSelection(pending, ev);
        }
        ev.stopPropagation();
    };

    const onPointerUp = (ev: PointerEvent) => {
        clearLongPress();
        // A tap (no travel) on empty space exits the active gizmo.
        if (pendingGizmoExit) {
            const moved = Math.hypot(ev.clientX - pendingGizmoExit.x, ev.clientY - pendingGizmoExit.y);
            pendingGizmoExit = null;
            if (moved < DRAG_START_PX) useCellBuilderStore.getState().setGizmoMode("none");
        }
        finalizeDrag(ev, false);
    };
    const onPointerCancel = (ev: PointerEvent) => {
        clearLongPress();
        pendingGizmoExit = null;
        finalizeDrag(ev, true);
    };

    // Desktop: right-click over a cell opens the same context menu.
    const onContextMenu = (ev: MouseEvent) => {
        const st = useCellBuilderStore.getState();
        if (!st.active) return;
        setPointer(ev as unknown as PointerEvent);
        const hit = pickBuilderMesh();
        if (!hit) return;
        ev.preventDefault();
        const cellId = hit.object.userData.__cellId as string;
        st.openContextMenu(ev.clientX, ev.clientY, cellId);
    };

    const onKeyDown = (ev: KeyboardEvent) => {
        const st = useCellBuilderStore.getState();
        if (!st.active) return;

        // Undo / redo — but not while typing in a form field (let the field's
        // own text undo win there).
        const target = ev.target as HTMLElement | null;
        const inField = !!target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
        if ((ev.ctrlKey || ev.metaKey) && !inField) {
            const k = ev.key.toLowerCase();
            if (k === "z" && !ev.shiftKey) {
                st.undo();
                ev.preventDefault();
                return;
            }
            if ((k === "z" && ev.shiftKey) || k === "y") {
                st.redo();
                ev.preventDefault();
                return;
            }
        }

        if (ev.key !== "Escape") return;
        // Escape unwinds one layer at a time: menu → gizmo → add-mode → selection.
        if (st.contextMenu) {
            st.closeContextMenu();
        } else if (st.gizmoMode !== "none") {
            st.setGizmoMode("none");
        } else if (st.mode !== "idle") {
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
    el.addEventListener("pointercancel", onPointerCancel, true);
    el.addEventListener("contextmenu", onContextMenu);
    window.addEventListener("keydown", onKeyDown);

    rebuild();
    rebuildPorts();
    syncBuilderGrid();
    syncGizmo();
    const unsub = useCellBuilderStore.subscribe((s, prev) => {
        if (s.cells !== prev.cells || s.active !== prev.active) rebuild();
        else if (s.selection !== prev.selection) refreshFaceStyles();
        if (
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.portsOverlayVisible !== prev.portsOverlayVisible ||
            s.equipmentTypes !== prev.equipmentTypes
        ) {
            rebuildPorts();
        }
        if (
            s.selection !== prev.selection ||
            s.gizmoMode !== prev.gizmoMode ||
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.gridStep !== prev.gridStep ||
            s.cellsVisible !== prev.cellsVisible
        ) {
            syncGizmo();
        }
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
        el.removeEventListener("pointercancel", onPointerCancel, true);
        el.removeEventListener("contextmenu", onContextMenu);
        window.removeEventListener("keydown", onKeyDown);
        if (controlsRef.current) controlsRef.current.enabled = true;
        clearLongPress();
        gizmo.detach();
        gizmo.dispose();
        scene.remove(gizmoHelper);
        disposeResizeHandles();
        hiddenDefaultGrids.forEach((g) => (g.visible = true));
        hiddenDefaultGrids.length = 0;
        disposeBuilderGrid();
        clearPorts();
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
