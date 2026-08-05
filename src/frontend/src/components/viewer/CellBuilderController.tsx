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
import {hexToInt, portColorInt, uniquePortColorHexByIndex} from "@/utils/portColor";
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
const OPENING_COLOR = 0xef4444; // red — a negative-volume door/window cut
const DEFAULT_CELL_SIZE: Vec3 = [5, 5, 3];
const DEFAULT_EQUIPMENT_SIZE: Vec3 = [1, 1, 1];
const DEFAULT_OPENING_SIZE: Vec3 = [1, 1, 2]; // door-ish; snaps to the wall it lands on

const colorForKind = (kind: "cell" | "equipment" | "opening"): number =>
    kind === "cell"
        ? CELL_COLOR
        : kind === "opening"
          ? OPENING_COLOR
          : EQUIPMENT_COLOR;
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
    // Shared unit-sphere for the nozzle-position markers (scaled per port);
    // per-port materials carry the port colour. Freed once at cleanup.
    const portMarkerGeom = new THREE.SphereGeometry(1, 12, 8);

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
        // Every cell in the multi-select set highlights (not just the primary),
        // so an add-mode selection shows what a Hide / copy will act on.
        const selectedSet = new Set(st.selectedCellIds);
        for (const [cellId, mesh] of meshById) {
            const cell = st.cells[cellId];
            if (!cell) continue;
            const base = colorForKind(cell.kind);
            const cellSelected = sel?.cellId === cellId || selectedSet.has(cellId);
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
                const color = colorForKind(cell.kind);
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
                // Equipment can carry a rotation (gizmo / manual panel). Spin the
                // box preview about the footprint centre so it matches the
                // compiled body; the box-centre orbits that pivot.
                const rot = cell.rotation;
                if (cell.kind === "equipment" && rot && (rot[0] || rot[1] || rot[2])) {
                    const euler = new THREE.Euler(
                        THREE.MathUtils.degToRad(rot[0]),
                        THREE.MathUtils.degToRad(rot[1]),
                        THREE.MathUtils.degToRad(rot[2]),
                        "ZYX",
                    );
                    mesh.quaternion.setFromEuler(euler);
                    const pivot = new THREE.Vector3(
                        cell.origin[0] + cell.size[0] / 2,
                        cell.origin[1] + cell.size[1] / 2,
                        cell.origin[2],
                    );
                    const offset = new THREE.Vector3(0, 0, cell.size[2] / 2).applyEuler(euler);
                    mesh.position.copy(pivot).add(offset);
                }
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
        applyCellVisibility();
        ghost.visible = false;
        ghostBox = null;
        refreshFaceStyles();
    };

    // Per-cell hide (the "Hide selected" analogue): a hidden cell's box is made
    // invisible; pickBuilderMesh also drops it so it stops absorbing clicks.
    const applyCellVisibility = () => {
        const hidden = useCellBuilderStore.getState().hiddenCellIds;
        for (const [cellId, mesh] of meshById) {
            mesh.visible = !hidden.includes(cellId);
        }
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
            // Nozzle markers share portMarkerGeom (freed at cleanup) but own
            // their material.
            else if (o instanceof THREE.Mesh) (o.material as THREE.Material).dispose();
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
            // Ports are local to the footprint centre; spin them with the same
            // ZYX rotation the compiler applies so the overlay tracks the placed
            // (rotated) nozzles.
            const rot = cell.rotation;
            const portEuler =
                cell.kind === "equipment" && rot && (rot[0] || rot[1] || rot[2])
                    ? new THREE.Euler(
                          THREE.MathUtils.degToRad(rot[0]),
                          THREE.MathUtils.degToRad(rot[1]),
                          THREE.MathUtils.degToRad(rot[2]),
                          "ZYX",
                      )
                    : null;
            for (let pi = 0; pi < ports.length; pi++) {
                const p = ports[pi];
                const pos = p.position ?? [0, 0, 0];
                const dv = p.direction_vector ?? [0, 0, 1];
                const localPos = new THREE.Vector3(pos[0], pos[1], pos[2]);
                if (portEuler) localPos.applyEuler(portEuler);
                // The nozzle position: where the port physically attaches.
                const nozzle = new THREE.Vector3(cx + localPos.x, cy + localPos.y, cz + localPos.z);
                // Colour by the port's index in the list → every I/O is unique.
                const color = portColorInt(p, pi);
                // direction_vector is the outward nozzle normal; the arrow shows
                // actual flow — INPUT points into the equipment, OUTPUT points
                // out, INOUT stays outward.
                const dir = new THREE.Vector3(dv[0], dv[1], dv[2]);
                if (portEuler) dir.applyEuler(portEuler);
                if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
                dir.normalize();
                if (p.direction === "IN") dir.negate();
                // Keep the whole arrow OUTSIDE the equipment box so it stays
                // visible, and always land a marker at the nozzle position.
                // Outward flow (OUT/INOUT): tail at the nozzle, tip points out.
                // Inward flow (IN): offset the tail outward by `len` so the TIP
                // lands exactly on the nozzle position and the shaft sits
                // outside the box rather than disappearing inside it.
                const tail =
                    p.direction === "IN"
                        ? nozzle.clone().addScaledVector(dir, -len)
                        : nozzle;
                const arrow = new THREE.ArrowHelper(dir, tail, len, color, len * 0.4, len * 0.25);
                arrow.traverse((o) => o.layers.set(1));
                portsGroup.add(arrow);
                // Nozzle-position marker: a small sphere at the attachment point
                // so the position is shown independently of the arrow tip.
                const marker = new THREE.Mesh(
                    portMarkerGeom,
                    new THREE.MeshBasicMaterial({color}),
                );
                marker.position.copy(nozzle);
                marker.scale.setScalar(len * 0.08);
                marker.layers.set(1);
                portsGroup.add(marker);
            }
        }
        // Site I/O terminals: a system connection can terminate at a model-boundary
        // site input/output (not an equipment port). Draw it like a port — an arrow
        // at its world position along its orientation — so the boundary interfaces
        // show up in the same overlay. Their positions are already world-space
        // (unlike equipment ports, which are cell-relative).
        const siteLen = 0.6;
        let siteIdx = 0;
        for (const sys of Object.values(st.systems)) {
            for (const conn of sys.connections) {
                if (!conn.site) continue;
                const pos = conn.position ?? [0, 0, 0];
                const dv = conn.directionVector ?? [0, 0, 1];
                const nozzle = new THREE.Vector3(pos[0], pos[1], pos[2]);
                const color = hexToInt(uniquePortColorHexByIndex(siteIdx++));
                // directionVector points into the model (the run's departure). An
                // input flows in along it; an output flows off-model, so its arrow
                // points the other way.
                const dir = new THREE.Vector3(dv[0], dv[1], dv[2]);
                if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
                dir.normalize();
                if (conn.direction === "OUT") dir.negate();
                const tail =
                    conn.direction === "OUT" ? nozzle.clone().addScaledVector(dir, -siteLen) : nozzle;
                const arrow = new THREE.ArrowHelper(dir, tail, siteLen, color, siteLen * 0.4, siteLen * 0.25);
                arrow.traverse((o) => o.layers.set(1));
                portsGroup.add(arrow);
                const marker = new THREE.Mesh(portMarkerGeom, new THREE.MeshBasicMaterial({color}));
                marker.position.copy(nozzle);
                marker.scale.setScalar(siteLen * 0.1);
                marker.layers.set(1);
                portsGroup.add(marker);
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
    // Read/seed the proxy's orientation in the same ZYX order the store + the
    // compiler compose rotations (Rz·Ry·Rx), so a per-axis rotation the gizmo
    // produces round-trips to identical ROT_X/Y/Z on the built equipment.
    gizmoProxy.rotation.order = "ZYX";
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
        if (!sel) return;
        const cell = st.cells[sel.cellId];
        if (!cell) return;
        if (st.gizmoMode === "translate") {
            const step = st.gridStep > 0 ? st.gridStep : 0.1;
            const origin = originFromCenter(
                [gizmoProxy.position.x, gizmoProxy.position.y, gizmoProxy.position.z],
                cell.size,
                step,
            );
            st.updateCell(cell.id, {origin});
        } else if (st.gizmoMode === "rotate") {
            // Proxy euler is ZYX (see gizmoProxy.rotation.order) → matches the
            // store/compiler; snap to 0.1° so it reads cleanly in the panel.
            const e = gizmoProxy.rotation;
            const deg = (v: number) => Math.round(THREE.MathUtils.radToDeg(v) * 10) / 10;
            st.setCellRotation(cell.id, [deg(e.x), deg(e.y), deg(e.z)]);
        }
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
        // Equipment is sized by its type, not free-resized in the scene — no
        // resize handles for it (Move still works).
        const show = !!(
            st.active && st.gizmoMode === "resize" && cell && cell.kind === "cell" && st.cellsVisible
        );
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
        // Rotate is equipment-only — spaces stay axis-aligned lattice boxes.
        const rotateOn = !!(
            st.active && st.gizmoMode === "rotate" && cell && cell.kind === "equipment" && st.cellsVisible
        );
        if (translateOn && cell) {
            gizmo.setTranslationSnap(st.gridStep > 0 ? st.gridStep : null);
            if (!gizmo.dragging) {
                gizmoProxy.rotation.set(0, 0, 0);
                gizmoProxy.position.set(
                    cell.origin[0] + cell.size[0] / 2,
                    cell.origin[1] + cell.size[1] / 2,
                    cell.origin[2] + cell.size[2] / 2,
                );
            }
            if (gizmo.object !== gizmoProxy) gizmo.attach(gizmoProxy);
            gizmo.setMode("translate");
            gizmoHelper.visible = true;
        } else if (rotateOn && cell) {
            // Rings snap to 15°; the manual panel supplies exact angles. The
            // proxy sits at the footprint centre (the compiler's pivot) and is
            // seeded from the cell's current rotation so the gizmo starts aligned.
            gizmo.setRotationSnap(THREE.MathUtils.degToRad(15));
            if (!gizmo.dragging) {
                gizmoProxy.position.set(
                    cell.origin[0] + cell.size[0] / 2,
                    cell.origin[1] + cell.size[1] / 2,
                    cell.origin[2],
                );
                const rot = cell.rotation ?? [0, 0, 0];
                gizmoProxy.rotation.set(
                    THREE.MathUtils.degToRad(rot[0]),
                    THREE.MathUtils.degToRad(rot[1]),
                    THREE.MathUtils.degToRad(rot[2]),
                );
            }
            if (gizmo.object !== gizmoProxy) gizmo.attach(gizmoProxy);
            gizmo.setMode("rotate");
            gizmoHelper.visible = true;
        } else {
            if (gizmo.object) gizmo.detach();
            gizmoHelper.visible = false;
        }
        // Axis lock (X/Y/Z keys / HUD) restricts the visible + usable gizmo
        // handle to one axis; null shows all three. Reset to all when detached so
        // a later attach isn't stuck on a stale constraint.
        if (translateOn || rotateOn) {
            const lock = st.gizmoAxisLock;
            gizmo.showX = lock === null || lock === 0;
            gizmo.showY = lock === null || lock === 1;
            gizmo.showZ = lock === null || lock === 2;
        } else {
            gizmo.showX = gizmo.showY = gizmo.showZ = true;
        }
        rebuildResizeHandles();
        requestRender();
    };

    // Begin a face drag (positive face scales size; negative face shifts origin).
    // ``immediate`` starts it right away (resize-handle grab) vs. after
    // DRAG_START_PX of travel (a face press, once face-drag resizing is on).
    const startFaceDrag = (cell: BuilderCell, faceIndex: number, ev: PointerEvent, immediate: boolean): boolean => {
        // Equipment is sized by its type — never face-drag-resized in the scene.
        if (cell.kind !== "cell") return false;
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

    // A tap on a cell that selects on pointerup — used when face-drag resizing
    // is OFF, where we must NOT grab/stop the pointer on pointerdown (so a drag
    // over a cell still orbits the camera). A no-travel pointerup resolves this
    // to a selection; any travel past DRAG_START_PX cancels it (it was an orbit).
    let pendingSelect: {cellId: string; faceIndex: number; x: number; y: number} | null = null;

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
        // A press on the translate/rotate gizmo's handle is a drag, not a long-press.
        if ((st.gizmoMode === "translate" || st.gizmoMode === "rotate") && gizmo.axis) return;
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
            pendingSelect = null;
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
        // Per-cell hidden boxes are excluded so a click passes through to
        // whatever geometry (e.g. the compiled result) sits underneath.
        // intersectObjects targets the meshes directly, bypassing the group, so
        // it ignores mesh.visible — filter explicitly.
        const meshes = [...meshById.values()].filter((m) => m.visible);
        const hits = raycaster.intersectObjects(meshes, false);
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

    const updateGhost = () => {
        const st = useCellBuilderStore.getState();
        const size =
            st.mode === "add-cell"
                ? DEFAULT_CELL_SIZE
                : st.mode === "add-opening"
                  ? DEFAULT_OPENING_SIZE
                  : DEFAULT_EQUIPMENT_SIZE;
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

        if (
            st.mode === "add-cell" ||
            st.mode === "add-equipment" ||
            st.mode === "add-opening"
        ) {
            updateGhost();
            if (ghostBox) {
                const kind =
                    st.mode === "add-cell"
                        ? "cell"
                        : st.mode === "add-opening"
                          ? "opening"
                          : "equipment";
                st.addCell(kind, ghostBox.origin, ghostBox.size);
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
            // Translate/rotate: TransformControls owns the pointer over its handles.
            if ((st.gizmoMode === "translate" || st.gizmoMode === "rotate") && gizmo.axis) return;
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

        // Face-drag resizing is opt-in. When it's OFF, never grab or stop the
        // pointer here: let OrbitControls own it so a drag anywhere — including
        // over a cell — orbits the camera. A no-travel tap still selects, via
        // pendingSelect resolved on pointerup.
        if (!st.faceDragResize) {
            pendingSelect = {cellId, faceIndex: hit.face.materialIndex, x: ev.clientX, y: ev.clientY};
            return;
        }

        // Resize enabled: begin a pending face-drag that becomes a real drag
        // after DRAG_START_PX of travel, or a selection click on a bare
        // pointerup. Stop propagation so the drag owns the pointer, not orbit.
        if (!startFaceDrag(cell, hit.face.materialIndex, ev, false)) return;
        ev.stopPropagation();
    };

    const resolveClickSelection = (target: {cellId: string; faceIndex: number}, ev: PointerEvent) => {
        const st = useCellBuilderStore.getState();
        // "none" mode: a plain click selects nothing (free navigation).
        if (st.selectMode === "none") return;
        const cell = st.cells[target.cellId];
        if (!cell) return;

        setPointer(ev);
        const hit = pickBuilderMesh();

        // Explicit selection: the panel's select-mode fully decides what a
        // click picks — no implicit border-proximity edge override. setSelection
        // surfaces the details in the Selected Object Info panel.
        if (st.selectMode === "edge") {
            // Nearest border edge of the clicked face (Infinity tolerance =
            // always resolve to the closest of the face's four borders).
            const edge = hit
                ? edgeHitOnFace(cell, target.faceIndex, worldToModel(hit.point), Infinity)
                : null;
            if (edge) {
                st.setSelection({kind: "edge", cellId: cell.id, faceIndex: target.faceIndex, edge});
            }
            return;
        }
        if (st.selectMode === "face") {
            st.setSelection({kind: "face", cellId: cell.id, faceIndex: target.faceIndex});
            return;
        }
        // Whole-cell pick: add-mode toggles the cell in the multi-select set (so
        // several cells can be copied/hidden at once); otherwise it's a single
        // selection.
        if (st.cellAddMode) {
            st.toggleCellSelection(cell.id);
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
        // A drag over a cell is an orbit, not a selection tap.
        if (pendingSelect &&
            Math.hypot(ev.clientX - pendingSelect.x, ev.clientY - pendingSelect.y) > DRAG_START_PX) {
            pendingSelect = null;
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

        if (
            st.mode === "add-cell" ||
            st.mode === "add-equipment" ||
            st.mode === "add-opening"
        ) {
            updateGhost();
            return;
        }

        if (st.mode === "idle") {
            // Explicit selection only: hovering never auto-highlights a
            // face/edge (that yellow hover pick read as an accidental
            // selection). The chosen element highlights on an explicit click;
            // hover just offers a cursor hint over a pickable cell.
            setHoveredEdge(null);
            setHoveredFace(null, -1);
            const overCell =
                st.selectMode !== "none" &&
                st.gizmoMode === "none" &&
                pickBuilderMesh() !== null;
            renderer.domElement.style.cursor =
                overCell ? (st.selectMode === "edge" ? "crosshair" : "pointer") : "";
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
        // A no-travel tap on a cell (face-drag resize off) resolves to a
        // selection. We never stopped propagation, so OrbitControls still
        // handled the pointer — a drag would have cleared pendingSelect above.
        if (pendingSelect) {
            const moved = Math.hypot(ev.clientX - pendingSelect.x, ev.clientY - pendingSelect.y);
            const target = pendingSelect;
            pendingSelect = null;
            if (moved < DRAG_START_PX) resolveClickSelection(target, ev);
        }
        finalizeDrag(ev, false);
    };
    const onPointerCancel = (ev: PointerEvent) => {
        clearLongPress();
        pendingGizmoExit = null;
        pendingSelect = null;
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

        // Bump the selected equipment up/down a cell floor (desktop shortcut).
        // PageUp = up a floor, PageDown = down; no-op without an equipment pick.
        if (!inField && (ev.key === "PageUp" || ev.key === "PageDown")) {
            if (st.selection) {
                st.bumpSelectedFloor(ev.key === "PageUp" ? 1 : -1);
                requestRender();
            }
            ev.preventDefault();
            return;
        }

        // --- Blender-style gizmo shortcuts (not while typing in a field) ------
        // These consume the key (stopPropagation) so the global viewer handler
        // — same phase, but this listener runs in capture — doesn't also fire.
        if (!inField && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            const k = ev.key.toLowerCase();
            const cell = st.selection ? st.cells[st.selection.cellId] : null;
            // Shift+H hides the selected builder cells (mirrors the Hide buttons).
            // With a builder selection this wins over the global mesh-range hide;
            // with nothing selected it falls through so result meshes still hide.
            if (ev.shiftKey && k === "h") {
                const ids = st.selectedCellIds.length
                    ? st.selectedCellIds
                    : cell
                      ? [cell.id]
                      : [];
                if (ids.length) {
                    st.hideCells(ids);
                    ev.preventDefault();
                    ev.stopPropagation();
                }
                return;
            }
            // Shift+U unhides all builder cells. Unlike hide, this does NOT stop
            // propagation — "unhide all" should reveal everything, so the global
            // handler still runs to unhide any hidden result meshes too.
            if (ev.shiftKey && k === "u") {
                st.unhideAllCells();
                return;
            }
            // G/R/S activate the translate / rotate / resize gizmo (Blender keys):
            // rotate is equipment-only, resize is cell-only (matches the menus).
            if (!ev.shiftKey && cell) {
                if (k === "g") {
                    st.setGizmoMode("translate");
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (k === "r" && cell.kind === "equipment") {
                    st.setGizmoMode("rotate");
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (k === "s" && cell.kind === "cell") {
                    st.setGizmoMode("resize");
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
            }
            // X/Y/Z lock the active translate/rotate gizmo to that axis (press the
            // same axis again to release). The HUD's numeric field then applies
            // along it.
            if (
                (st.gizmoMode === "translate" || st.gizmoMode === "rotate") &&
                (k === "x" || k === "y" || k === "z")
            ) {
                const axis = k === "x" ? 0 : k === "y" ? 1 : 2;
                st.setGizmoAxisLock(st.gizmoAxisLock === axis ? null : axis);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
        }

        // Escape while typing in a field (the HUD's numeric inputs) blurs the
        // field — don't also unwind the selection/gizmo underneath.
        if (ev.key !== "Escape" || inField) return;
        // The insert popover owns its own Escape (it closes itself); don't also
        // unwind the selection underneath it.
        if (st.insertMenu) return;
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
    // Capture phase so the builder's Shift+H / gizmo keys can preempt (and
    // stopPropagation) the global viewer key handler, which listens on bubble.
    window.addEventListener("keydown", onKeyDown, true);

    rebuild();
    rebuildPorts();
    syncBuilderGrid();
    syncGizmo();
    const unsub = useCellBuilderStore.subscribe((s, prev) => {
        if (s.cells !== prev.cells || s.active !== prev.active) rebuild();
        else if (s.selection !== prev.selection || s.selectedCellIds !== prev.selectedCellIds)
            refreshFaceStyles();
        if (
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.portsOverlayVisible !== prev.portsOverlayVisible ||
            s.equipmentTypes !== prev.equipmentTypes ||
            s.systems !== prev.systems
        ) {
            rebuildPorts();
        }
        if (
            s.selection !== prev.selection ||
            s.gizmoMode !== prev.gizmoMode ||
            s.gizmoAxisLock !== prev.gizmoAxisLock ||
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
        if (s.hiddenCellIds !== prev.hiddenCellIds) {
            applyCellVisibility();
            requestRender();
        }
        if (
            s.mode !== prev.mode &&
            s.mode !== "add-cell" &&
            s.mode !== "add-equipment" &&
            s.mode !== "add-opening"
        ) {
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
        window.removeEventListener("keydown", onKeyDown, true);
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
        portMarkerGeom.dispose();
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
