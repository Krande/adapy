import React from "react";
import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {ungzip} from "pako";

import {cameraRef, controlsRef, rendererRef, sceneRef} from "@/state/refs";
import {portSnapTargets, portsForEquipment} from "@/utils/cellbuilder/ports";
import {requestRender} from "@/state/perfStore";
import {viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {useCompanionModelStore} from "@/state/companionModelStore";
import {bandFaceIds, stationRingPoints} from "@/utils/cellbuilder/loft";
import {hexToInt, portColorInt, uniquePortColorHexByIndex} from "@/utils/portColor";
import {
    applyFaceOffset,
    BOX_FACE_SIDES,
    cadDisplayBox,
    edgeEndpoints,
    edgeHitOnFace,
    extrudeBox,
    faceCenter,
    neighbourFaceInDirection,
    openingBoxOnFace,
    originFromCenter,
    quantize,
    snapBox,
    boxCorners,
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
const LOFT_COLOR = 0x14b8a6; // teal — a read-only swept-band (loft) proxy
const EXCLUDED_FACE_COLOR = 0x64748b; // slate — a removed (excluded) loft panel
const DEFAULT_EQUIPMENT_SIZE: Vec3 = [1, 1, 1];
// Last-resort box extents used ONLY when the cell/opening catalog is unreachable
// (the engine-advertised type otherwise supplies the size — see addModeSize).
const FALLBACK_CELL_SIZE: Vec3 = [5, 5, 3];
const FALLBACK_OPENING_SIZE: Vec3 = [1, 1, 2]; // door-ish; snaps to the wall it lands on

// The default box extent for the current add mode, taken from the selected
// engine-advertised cell/opening type (falling back to a sane constant only if
// the catalog couldn't be fetched). Equipment is sized from its own type/catalog
// at compile, so it keeps the unit default here.
function addModeSize(st: {
    mode: string;
    cellTypes: {slug: string; size: [number, number, number]}[];
    selectedCellType: string | null;
    openingTypes: {slug: string; size: [number, number, number]}[];
    selectedOpeningType: string | null;
}): Vec3 {
    if (st.mode === "add-cell") {
        const t = st.cellTypes.find((x) => x.slug === st.selectedCellType);
        return t ? [t.size[0], t.size[1], t.size[2]] : FALLBACK_CELL_SIZE;
    }
    if (st.mode === "add-opening") {
        const t = st.openingTypes.find((x) => x.slug === st.selectedOpeningType);
        return t ? [t.size[0], t.size[1], t.size[2]] : FALLBACK_OPENING_SIZE;
    }
    return DEFAULT_EQUIPMENT_SIZE;
}

const colorForKind = (kind: BuilderCell["kind"]): number =>
    kind === "cell"
        ? CELL_COLOR
        : kind === "opening"
          ? OPENING_COLOR
          : kind === "loft"
            ? LOFT_COLOR
            : EQUIPMENT_COLOR;
const BASE_OPACITY = 0.3;
const LOFT_OPACITY = 0.35;
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


// --- Loft band geometry (read-only swept proxy) ----------------------------
// Build a translucent swept mesh between a band's two profile rings (model-space
// absolute points — the mesh sits at the container origin, which carries the
// model offset, exactly like box cells). Side walls pair the two rings around
// the loop (equal ring counts = a clean quad strip; differing counts pair by
// proportional index so a rectangle->circle band still closes without crashing).
// Convex end caps (fans) close the bay cheaply. DoubleSide so winding is moot.
function sweptBandGeometry(lo: Vec3[], hi: Vec3[]): THREE.BufferGeometry {
    const n0 = lo.length;
    const n1 = hi.length;
    const positions: number[] = [];
    for (const p of lo) positions.push(p[0], p[1], p[2]);
    for (const p of hi) positions.push(p[0], p[1], p[2]);
    const hiBase = n0;
    const indices: number[] = [];
    const K = Math.max(n0, n1);
    for (let k = 0; k < K; k++) {
        const a0 = Math.floor((k * n0) / K) % n0;
        const a1 = Math.floor(((k + 1) * n0) / K) % n0;
        const b0 = hiBase + (Math.floor((k * n1) / K) % n1);
        const b1 = hiBase + (Math.floor(((k + 1) * n1) / K) % n1);
        indices.push(a0, a1, b1, a0, b1, b0);
    }
    // End caps: fan-triangulate each (convex) ring.
    for (let i = 1; i < n0 - 1; i++) indices.push(0, i, i + 1);
    for (let i = 1; i < n1 - 1; i++) indices.push(hiBase, hiBase + i, hiBase + i + 1);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geo.setIndex(indices);
    geo.computeVertexNormals();
    return geo;
}

// Per-face-pickable swept mesh (Phase 3b): one geometry group per profile side
// panel (edge k = ring vertex k -> k+1), plus a cap_lo fan and a cap_hi fan, so
// a raycast face's materialIndex maps 1:1 to a loft face id in the order
// bandFaceIds returns (edge0..edge_{n-1}, cap_lo, cap_hi). Requires equal ring
// counts (the homogeneous rectangle/circle bands the backend numbers); returns
// null for mismatched rings so the caller degrades to the single-material
// proportional mesh (whole-band pick only).
function sweptBandGroupedGeometry(lo: Vec3[], hi: Vec3[]): THREE.BufferGeometry | null {
    const n = lo.length;
    if (n < 3 || hi.length !== n) return null;
    const positions: number[] = [];
    for (const p of lo) positions.push(p[0], p[1], p[2]);
    for (const p of hi) positions.push(p[0], p[1], p[2]);
    const hiBase = n;
    const indices: number[] = [];
    const geo = new THREE.BufferGeometry();
    let cursor = 0;
    // Side panels: material index k = profile edge k.
    for (let k = 0; k < n; k++) {
        const a0 = k;
        const a1 = (k + 1) % n;
        const b0 = hiBase + k;
        const b1 = hiBase + ((k + 1) % n);
        indices.push(a0, a1, b1, a0, b1, b0);
        geo.addGroup(cursor, 6, k);
        cursor += 6;
    }
    // cap_lo fan (material n), cap_hi fan (material n+1).
    const capLoStart = cursor;
    for (let i = 1; i < n - 1; i++) {
        indices.push(0, i, i + 1);
        cursor += 3;
    }
    geo.addGroup(capLoStart, cursor - capLoStart, n);
    const capHiStart = cursor;
    for (let i = 1; i < n - 1; i++) {
        indices.push(hiBase, hiBase + i, hiBase + i + 1);
        cursor += 3;
    }
    geo.addGroup(capHiStart, cursor - capHiStart, n + 1);
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geo.setIndex(indices);
    geo.computeVertexNormals();
    return geo;
}

// Line segments tracing the two closed station rings (the band's edge overlay).
function ringsEdgesGeometry(lo: Vec3[], hi: Vec3[]): THREE.BufferGeometry {
    const pts: number[] = [];
    for (const ring of [lo, hi]) {
        const n = ring.length;
        for (let i = 0; i < n; i++) {
            const a = ring[i];
            const b = ring[(i + 1) % n];
            pts.push(a[0], a[1], a[2], b[0], b[1], b[2]);
        }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return geo;
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

    // Companion models: other procedural models shown ALONGSIDE the edited one.
    // Their own subgroup, and deliberately outside `cellsGroup` — the editable
    // cells carry picking, gizmos, hover and selection through meshById, and a
    // companion must carry none of that. Keeping them in separate groups means
    // the read-only ones cannot be hit by a raycast that only ever walks
    // cellsGroup, rather than relying on a flag that some future traversal
    // forgets to check.
    const companionsGroup = new THREE.Group();
    companionsGroup.name = "__cellbuilder_companions__";
    container.add(companionsGroup);

    // Port/nozzle overlay: coloured arrows at each placed equipment's I/O
    // positions/vectors. Its own subgroup (toggled independently of the cells)
    // and inherits the container's model offset so glyphs align with the
    // compiled structure.
    const portsGroup = new THREE.Group();
    container.add(portsGroup);

    // "Show as CAD" per-object previews: the equipment type's preview GLB seated
    // at the cell placement, replacing that cell's placeholder box. Its own
    // subgroup (inherits the container's model offset). `cadPreviewCache` holds a
    // parsed prototype per type id (clones share its geometry, so clones are
    // removed but NOT disposed — only the cache prototypes are freed at cleanup);
    // `cadPreviewShown` tracks which cells currently render CAD so their box is
    // hidden.
    const cadPreviewGroup = new THREE.Group();
    cadPreviewGroup.name = "__cad_preview__";
    container.add(cadPreviewGroup);
    const cadPreviewCache = new Map<string, THREE.Group | "loading" | "error">();
    const cadPreviewShown = new Set<string>();

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

    // Always-on-top overlay quad for the SELECTED box-cell face: a bright fill
    // drawn with depthTest off + a high renderOrder, so the picked face is fully
    // visible even THROUGH the cell body (a material tint on the shared box mesh
    // can't reliably beat its own near faces' draw order — hence a separate mesh).
    const faceOverlay = new THREE.Mesh(
        new THREE.BufferGeometry(),
        new THREE.MeshBasicMaterial({
            color: SELECTED_FACE_COLOR,
            transparent: true,
            opacity: 0.6,
            depthTest: false,
            depthWrite: false,
            side: THREE.DoubleSide,
        }),
    );
    faceOverlay.userData.__excludeFromFit = true;
    faceOverlay.renderOrder = 5;
    faceOverlay.visible = false;
    container.add(faceOverlay);
    const FACE_OVERLAY_IDX = new Uint16Array([0, 1, 2, 0, 2, 3]);
    // Position/size the overlay on the selected box face (model space), or hide.
    const updateFaceOverlay = () => {
        const st = useCellBuilderStore.getState();
        const sel = st.selection;
        const cell = sel ? st.cells[sel.cellId] : null;
        if (
            !cell ||
            cell.kind !== "cell" ||
            sel?.kind !== "face" ||
            sel.faceIndex == null ||
            !BOX_FACE_SIDES[sel.faceIndex] ||
            !st.cellsVisible
        ) {
            if (faceOverlay.visible) faceOverlay.visible = false;
            return;
        }
        const side = BOX_FACE_SIDES[sel.faceIndex];
        const [a1, a2] = ([0, 1, 2] as const).filter((a) => a !== side.axis) as [
            0 | 1 | 2,
            0 | 1 | 2,
        ];
        const base: Vec3 = [...cell.origin];
        if (side.positive) base[side.axis] += cell.size[side.axis];
        const c0: Vec3 = [...base];
        const c1: Vec3 = [...base];
        c1[a1] += cell.size[a1];
        const c2: Vec3 = [...c1];
        c2[a2] += cell.size[a2];
        const c3: Vec3 = [...base];
        c3[a2] += cell.size[a2];
        const pos = new Float32Array([...c0, ...c1, ...c2, ...c3]);
        faceOverlay.geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
        faceOverlay.geometry.setIndex(new THREE.BufferAttribute(FACE_OVERLAY_IDX, 1));
        faceOverlay.geometry.computeBoundingSphere();
        faceOverlay.visible = true;
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
            // Loft band (Phase 3b): per-face pickable when built with material
            // groups (one per side panel + cap) — highlight the picked panel and
            // dim excluded ones. A mismatched-count band has a single material
            // (whole-cell select/hover only). Either way the ring overlay tracks
            // selection.
            if (cell.kind === "loft") {
                const excluded = new Set(cell.loft?.excludeFaces ?? []);
                const faceIds = mesh.userData.__loftFaceIds as string[] | undefined;
                if (Array.isArray(mesh.material) && faceIds) {
                    const mats = mesh.material as THREE.MeshBasicMaterial[];
                    let hasThroughFace = false;
                    for (let fi = 0; fi < mats.length; fi++) {
                        const isExcluded = excluded.has(faceIds[fi]);
                        let color = base;
                        let opacity = cellSelected ? 0.5 : LOFT_OPACITY;
                        let through = false;
                        if (cellSelected && sel?.kind === "face" && sel.faceIndex === fi) {
                            color = SELECTED_FACE_COLOR;
                            opacity = 0.7;
                            through = true;
                        }
                        if (hovered?.mesh === mesh && hovered.faceIndex === fi) {
                            color = HOVER_FACE_COLOR;
                            opacity = 0.6;
                        }
                        // Removed panel: grey wireframe, barely visible — but keep
                        // the pick/hover tint so the selected excluded face still
                        // reads (its panel row is highlighted in the info panel).
                        if (isExcluded) {
                            if (color === base) color = EXCLUDED_FACE_COLOR;
                            opacity = 0.12;
                        }
                        mats[fi].wireframe = isExcluded;
                        mats[fi].color.setHex(color);
                        mats[fi].opacity = opacity;
                        mats[fi].depthTest = !through; // selected face draws through
                        if (through) hasThroughFace = true;
                    }
                    mesh.renderOrder = hasThroughFace ? 3 : 0;
                } else {
                    const m = mesh.material as THREE.MeshBasicMaterial;
                    let color = base;
                    let opacity = cellSelected ? 0.5 : LOFT_OPACITY;
                    if (hovered?.mesh === mesh) {
                        color = HOVER_FACE_COLOR;
                        opacity = 0.6;
                    }
                    m.color.setHex(color);
                    m.opacity = opacity;
                }
                const loftEdges = mesh.children[0] as THREE.LineSegments | undefined;
                if (loftEdges) {
                    (loftEdges.material as THREE.LineBasicMaterial).color.setHex(
                        cellSelected ? 0xffffff : base,
                    );
                }
                continue;
            }
            const mats = mesh.material as THREE.MeshBasicMaterial[];
            for (let fi = 0; fi < mats.length; fi++) {
                let color = base;
                let opacity = BASE_OPACITY;
                if (cellSelected) opacity = 0.4;
                // The selected face is tinted here (front-facing) AND drawn as an
                // always-on-top overlay quad (faceOverlay) so it's fully visible
                // even through the cell body — see updateFaceOverlay.
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
        updateFaceOverlay();
        requestRender();
    };

    /** Draw every companion showing topology as plain, non-interactive boxes.
     *
     * Rebuilt wholesale on change: a companion set is a handful of models, the
     * geometry is boxes, and an incremental diff here would be complexity
     * bought against a cost nobody can measure. */
    const rebuildCompanions = () => {
        for (let i = companionsGroup.children.length - 1; i >= 0; i--) {
            const o = companionsGroup.children[i];
            o.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(m);
            });
            companionsGroup.remove(o);
        }

        const {companions} = useCompanionModelStore.getState();
        for (const c of Object.values(companions)) {
            if (c.rep !== "topology") continue;
            // One group per model so its offset is a single transform rather
            // than baked into every box — moving it is then a position write.
            const modelGroup = new THREE.Group();
            modelGroup.name = `__companion__${c.modelId}`;
            modelGroup.position.x = c.offsetX;
            for (const cell of c.cells) {
                const geo = new THREE.BoxGeometry(cell.size[0], cell.size[1], cell.size[2]);
                const mesh = new THREE.Mesh(
                    geo,
                    new THREE.MeshBasicMaterial({
                        color: colorForKind(cell.kind),
                        transparent: true,
                        // Dimmer than the edited model: at a glance, which one
                        // you are editing must be unambiguous.
                        opacity: BASE_OPACITY * 0.45,
                        depthWrite: false,
                    }),
                );
                mesh.position.set(
                    cell.origin[0] + cell.size[0] / 2,
                    cell.origin[1] + cell.size[1] / 2,
                    cell.origin[2] + cell.size[2] / 2,
                );
                // Not pickable, not fittable: a companion is scenery.
                mesh.raycast = () => {};
                mesh.userData.__excludeFromFit = true;
                modelGroup.add(mesh);
            }
            companionsGroup.add(modelGroup);
        }
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
                // Loft band: a read-only swept proxy drawn from its two profile
                // rings, with the two station rings as the edge overlay. Same
                // meshById/__cellId plumbing as a box, so click-select + hide +
                // the whole-cell highlight all work identically — only the
                // geometry source differs (band instead of BoxGeometry).
                if (cell.kind === "loft" && cell.loft) {
                    const [lo, hi] = cell.loft.rings;
                    // Per-face pickable when the two rings match (homogeneous
                    // rectangle/circle band): one material per side panel + the
                    // two caps, so a face pick resolves to a loft face id. A
                    // mismatched-count band degrades to a single translucent
                    // material (whole-band pick only). DoubleSide so winding is moot.
                    const grouped = sweptBandGroupedGeometry(lo, hi);
                    const loftMat = () =>
                        new THREE.MeshBasicMaterial({
                            color: LOFT_COLOR,
                            transparent: true,
                            opacity: LOFT_OPACITY,
                            depthWrite: false,
                            side: THREE.DoubleSide,
                        });
                    let mesh: THREE.Mesh;
                    if (grouped) {
                        const {edges, caps} = bandFaceIds(cell.loft);
                        const faceIds = [...edges, caps[0], caps[1]];
                        mesh = new THREE.Mesh(
                            grouped,
                            faceIds.map(() => loftMat()),
                        );
                        // Member-relative loft face id per material index (drives
                        // per-face selection highlight + excluded-panel dimming).
                        mesh.userData.__loftFaceIds = faceIds;
                    } else {
                        mesh = new THREE.Mesh(sweptBandGeometry(lo, hi), loftMat());
                    }
                    mesh.userData.__cellId = cell.id;
                    const ringEdges = new THREE.LineSegments(
                        ringsEdgesGeometry(lo, hi),
                        new THREE.LineBasicMaterial({color: LOFT_COLOR}),
                    );
                    mesh.add(ringEdges);
                    cellsGroup.add(mesh);
                    meshById.set(cell.id, mesh);
                    continue;
                }
                // A CAD-backed equipment fits its editable box to the loaded CAD
                // mesh's bounds (so the box wraps the real geometry, not the
                // declared LX/LY/LZ); everything else uses its declared box.
                const {box: dbox, cadFitted} = displayBoxForCell(cell);
                const geo = new THREE.BoxGeometry(...dbox.size);
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
                    dbox.origin[0] + dbox.size[0] / 2,
                    dbox.origin[1] + dbox.size[1] / 2,
                    dbox.origin[2] + dbox.size[2] / 2,
                );
                // Equipment can carry a rotation (gizmo / manual panel). Spin the
                // box preview about the footprint centre so it matches the
                // compiled body; the box-centre orbits that pivot. A CAD-fitted
                // box already wraps the placed (rotated) geometry — its AABB has
                // the rotation baked in — so it stays axis-aligned here.
                const rot = cell.rotation;
                if (!cadFitted && cell.kind === "equipment" && rot && (rot[0] || rot[1] || rot[2])) {
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
            // A cell shown as CAD hides its placeholder box (the CAD stands in).
            mesh.visible = !hidden.includes(cellId) && !cadPreviewShown.has(cellId);
        }
    };

    // ── "Show as CAD" per-object previews ─────────────────────────────
    const previewScope = (): string => {
        const s = useScopeStore.getState().current;
        return s ? scopeUrlPart(s) : "user:me";
    };

    // The catalog type id for an equipment cell (needed to fetch its preview
    // GLB), resolved by slug then name like portsForEquipment. Built-in code
    // archetypes have no id (and no CAD), so those yield null.
    const cadTypeIdForCell = (cell: BuilderCell): string | null => {
        if (cell.kind !== "equipment" || !cell.equipmentType) return null;
        const types = useCellBuilderStore.getState().equipmentTypes;
        const key = cell.equipmentType.toLowerCase();
        const t =
            types.find((o) => o.slug.toLowerCase() === key) ?? types.find((o) => o.name.toLowerCase() === key);
        return t?.id ?? null;
    };

    // Parse an equipment preview GLB blob into a group (gzip-sniffed, like the
    // catalogue preview + the result loader). Displayed in its NATIVE orientation
    // (no re-orientation) and its placement is fit from its measured bounds — we
    // do NOT assume the (possibly old) preview GLB is Z-up.
    const parseCadGlb = async (scope: string, key: string): Promise<THREE.Group | null> => {
        const buf = await viewerApi.getBlob(scope, key);
        let bytes: Uint8Array<ArrayBufferLike> = new Uint8Array(buf);
        if (bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) bytes = ungzip(bytes);
        const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
        return await new Promise((resolve) => {
            new GLTFLoader().parse(
                ab,
                "",
                (gltf) => resolve(gltf.scene),
                () => resolve(null),
            );
        });
    };

    // Lazily fetch + cache a type's preview GLB, then re-run the CAD rebuild.
    const ensureCadLoaded = (typeId: string) => {
        if (cadPreviewCache.has(typeId)) return;
        cadPreviewCache.set(typeId, "loading");
        void (async () => {
            try {
                const scope = previewScope();
                const detail = await viewerApi.getEquipmentType(scope, typeId);
                if (!detail.preview_glb_key) {
                    cadPreviewCache.set(typeId, "error");
                } else {
                    const g = await parseCadGlb(scope, detail.preview_glb_key);
                    cadPreviewCache.set(typeId, g ?? "error");
                }
            } catch {
                cadPreviewCache.set(typeId, "error");
            }
            rebuildCadPreviews();
        })();
    };

    // Clone the cached prototype and seat its min corner at the cell origin (the
    // compiler's "min corner → cell corner" convention), then spin it about the
    // footprint centre to match the cell rotation — mirroring the box placement.
    const placeCadPreview = (proto: THREE.Group, cell: BuilderCell): THREE.Object3D | null => {
        const g = proto.clone(true);
        g.position.set(0, 0, 0);
        g.rotation.set(0, 0, 0);
        g.updateWorldMatrix(true, true);
        const b = new THREE.Box3().setFromObject(g);
        if (b.isEmpty()) return null;
        const seat = new THREE.Vector3(cell.origin[0] - b.min.x, cell.origin[1] - b.min.y, cell.origin[2] - b.min.z);
        g.position.copy(seat);
        const rot = cell.rotation;
        if (rot && (rot[0] || rot[1] || rot[2])) {
            const euler = new THREE.Euler(
                THREE.MathUtils.degToRad(rot[0]),
                THREE.MathUtils.degToRad(rot[1]),
                THREE.MathUtils.degToRad(rot[2]),
                "ZYX",
            );
            const pivot = new THREE.Vector3(
                cell.origin[0] + cell.size[0] / 2,
                cell.origin[1] + cell.size[1] / 2,
                cell.origin[2],
            );
            const holder = new THREE.Group();
            holder.position.copy(pivot);
            holder.setRotationFromEuler(euler);
            g.position.sub(pivot); // re-express seat relative to the pivot holder
            holder.add(g);
            return holder;
        }
        return g;
    };

    const rebuildCadPreviews = () => {
        // Clones share the cached prototype's geometry/materials — remove them
        // without disposing (the cache owns those resources; freed at teardown).
        for (let i = cadPreviewGroup.children.length - 1; i >= 0; i--) {
            cadPreviewGroup.remove(cadPreviewGroup.children[i]);
        }
        cadPreviewShown.clear();
        const st = useCellBuilderStore.getState();
        if (st.active) {
            for (const cellId of st.cadPreviewCells) {
                const cell = st.cells[cellId];
                if (!cell || cell.kind !== "equipment") continue;
                const typeId = cadTypeIdForCell(cell);
                if (!typeId) continue;
                const cached = cadPreviewCache.get(typeId);
                if (cached === undefined) {
                    ensureCadLoaded(typeId);
                    continue;
                }
                if (cached === "loading" || cached === "error") continue;
                const placed = placeCadPreview(cached, cell);
                if (placed) {
                    cadPreviewGroup.add(placed);
                    cadPreviewShown.add(cellId);
                }
            }
        }
        cadPreviewGroup.visible = st.cellsVisible;
        applyCellVisibility();
        requestRender();
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
                // Tag every part of the arrow (+ its marker) with the port
                // identity so a right-click can resolve which equipment port it
                // hit and open the port edit menu. Layer 1 keeps them out of the
                // normal (layer-0) cell/face pick.
                arrow.traverse((o) => {
                    o.layers.set(1);
                    o.userData.__portCellId = cell.id;
                    o.userData.__portName = p.name;
                });
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
                marker.userData.__portCellId = cell.id;
                marker.userData.__portName = p.name;
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

    // Baseline for the loft member-move gizmo: the last APPLIED proxy position
    // (model frame). moveLoftMember advances it by the quantized delta each
    // frame, so residual sub-grid pointer travel carries over and the member
    // steps in exact grid multiples. Null between drags (re-seeded on start).
    let loftDragLast: THREE.Vector3 | null = null;

    const gizmo = new TransformControls(cameraRef.current ?? (camera as THREE.Camera), renderer.domElement);
    gizmo.setSpace("world");
    const gizmoHelper = gizmo.getHelper();
    gizmoHelper.userData.__excludeFromFit = true;
    gizmoHelper.visible = false;
    scene.add(gizmoHelper);

    // --- Blender-style axis-locked modal move ---------------------------------
    // When the translate gizmo is locked to an axis (X/Y/Z), the cell tracks the
    // pointer along that axis with NO click-drag — move the mouse and it follows;
    // left-click confirms, Escape cancels. This makes vertex snapping obvious
    // (it's evaluated every pointer move) and matches Blender's G-then-X grab.
    // `startT` is seeded on the first move so the cell doesn't jump on activation.
    let modalMove:
        | {
              cellId: string;
              axis: 0 | 1 | 2;
              lineOrigin: THREE.Vector3; // world-space point on the constraint axis
              lineDir: THREE.Vector3; // unit axis direction
              startT: number | null; // ray param at grab start (null until first move)
              startBox: CellBox; // pre-move box, for cancel
          }
        | null = null;
    // A coloured line through the grab point showing the constraint axis.
    const guideLine = new THREE.Line(
        new THREE.BufferGeometry(),
        new THREE.LineBasicMaterial({depthTest: false, transparent: true, opacity: 0.6}),
    );
    guideLine.userData.__excludeFromFit = true;
    guideLine.visible = false;
    guideLine.renderOrder = 2;
    container.add(guideLine);

    const showGuideLine = (axis: 0 | 1 | 2, centerModel: Vec3) => {
        const dir: Vec3 = [axis === 0 ? 1 : 0, axis === 1 ? 1 : 0, axis === 2 ? 1 : 0];
        const BIG = 1000;
        const pts = new Float32Array([
            centerModel[0] - dir[0] * BIG,
            centerModel[1] - dir[1] * BIG,
            centerModel[2] - dir[2] * BIG,
            centerModel[0] + dir[0] * BIG,
            centerModel[1] + dir[1] * BIG,
            centerModel[2] + dir[2] * BIG,
        ]);
        guideLine.geometry.setAttribute("position", new THREE.BufferAttribute(pts, 3));
        guideLine.geometry.computeBoundingSphere();
        (guideLine.material as THREE.LineBasicMaterial).color.setHex(HANDLE_AXIS_COLOR[axis]);
        guideLine.visible = true;
    };

    const cellCenterModel = (cell: BuilderCell): Vec3 => [
        cell.origin[0] + cell.size[0] / 2,
        cell.origin[1] + cell.size[1] / 2,
        cell.origin[2] + cell.size[2] / 2,
    ];

    // Equipment cells whose CENTRE sits inside `cell`'s box (all 3 axes, so the
    // two floors of a stacked model don't grab each other's units) — these ride
    // along when the space cell is translated. Captured once at drag start.
    const equipContainedIn = (cell: BuilderCell): string[] => {
        if (cell.kind !== "cell" || !useCellBuilderStore.getState().moveEquipWithCell) return [];
        const [ox, oy, oz] = cell.origin;
        const [dx, dy, dz] = cell.size;
        const ids: string[] = [];
        for (const c of Object.values(useCellBuilderStore.getState().cells)) {
            if (c.kind !== "equipment") continue;
            const cx = c.origin[0] + c.size[0] / 2;
            const cy = c.origin[1] + c.size[1] / 2;
            const cz = c.origin[2] + c.size[2] / 2;
            if (cx >= ox && cx <= ox + dx && cy >= oy && cy <= oy + dy && cz >= oz && cz <= oz + dz)
                ids.push(c.id);
        }
        return ids;
    };
    // The equipment captured to ride along with the cell being translated (gizmo
    // drag or axis-locked modal move). Empty when not translating a space cell.
    let translateEquip: string[] = [];

    // Apply a translate result: carry contained equipment when moving a space
    // cell, else a plain cell move.
    const applyCellTranslate = (cell: BuilderCell, origin: Vec3) => {
        const st = useCellBuilderStore.getState();
        if (cell.kind === "cell" && translateEquip.length)
            st.moveCellAndEquipment(cell.id, origin, translateEquip);
        else st.updateCell(cell.id, { origin });
    };

    // --- Vertex-snap indicator ------------------------------------------------
    // A hollow amber square drawn at the neighbour vertex the dragged cell just
    // snapped onto, so vertex magnetism is visible while moving. It's a Sprite
    // (always faces the camera) with constant on-screen size (sizeAttenuation
    // off), depth-test off so it shows through geometry. Lives in world space
    // (not the container) — position is the snapped corner + the model offset.
    const makeSnapTexture = (): THREE.CanvasTexture => {
        const cv = document.createElement("canvas");
        cv.width = cv.height = 64;
        const g = cv.getContext("2d")!;
        g.clearRect(0, 0, 64, 64);
        g.strokeStyle = "#fbbf24"; // amber-400 — reads against the blue cells
        g.lineWidth = 6;
        g.strokeRect(9, 9, 46, 46);
        g.fillStyle = "#fbbf24";
        g.fillRect(29, 29, 6, 6); // centre pip
        const tex = new THREE.CanvasTexture(cv);
        tex.needsUpdate = true;
        return tex;
    };
    const snapTex = makeSnapTexture();
    const snapMarker = new THREE.Sprite(
        new THREE.SpriteMaterial({map: snapTex, depthTest: false, transparent: true, sizeAttenuation: false}),
    );
    snapMarker.scale.set(0.05, 0.05, 1); // ~constant screen size
    snapMarker.renderOrder = 6;
    snapMarker.userData.__excludeFromFit = true;
    snapMarker.visible = false;
    scene.add(snapMarker);

    // Show the marker at a snapped neighbour vertex (model-space), or hide it.
    const showSnapMarker = (targetModel: Vec3 | null) => {
        if (!targetModel) {
            if (snapMarker.visible) {
                snapMarker.visible = false;
                requestRender();
            }
            return;
        }
        const off = offsetVec();
        snapMarker.position.set(targetModel[0] + off.x, targetModel[1] + off.y, targetModel[2] + off.z);
        snapMarker.visible = true;
        requestRender();
    };

    // Pixel radius (screen space) within which the pointer "catches" a neighbour
    // vertex. Screen-space so it behaves the same whether zoomed in or out — the
    // old world-distance snap drifted and matched far-away corners on zoom-out.
    const SNAP_PX = 22;

    // The neighbour-cell corner (model space) nearest the pointer on screen,
    // within SNAP_PX, or null. This is the snap TARGET — the vertex under the
    // cursor — so the marker lands exactly where the user is pointing.
    const nearestCornerToPointer = (excludeCellId: string): Vec3 | null => {
        const cam = cameraRef.current ?? (camera as THREE.PerspectiveCamera);
        const off = offsetVec();
        const rect = renderer.domElement.getBoundingClientRect();
        const px = (pointer.x * 0.5 + 0.5) * rect.width;
        const py = (-pointer.y * 0.5 + 0.5) * rect.height;
        const v = new THREE.Vector3();
        let best: Vec3 | null = null;
        let bestD = SNAP_PX;
        for (const c of Object.values(useCellBuilderStore.getState().cells)) {
            if (c.id === excludeCellId) continue;
            for (const corner of boxCorners({origin: c.origin, size: c.size})) {
                v.set(corner[0] + off.x, corner[1] + off.y, corner[2] + off.z).project(cam);
                if (v.z < -1 || v.z > 1) continue; // behind camera / clipped
                const sx = (v.x * 0.5 + 0.5) * rect.width;
                const sy = (-v.y * 0.5 + 0.5) * rect.height;
                const d = Math.hypot(sx - px, sy - py);
                if (d <= bestD) {
                    bestD = d;
                    best = corner;
                }
            }
        }
        return best;
    };

    // Origin (min corner) for a cell whose centre is dragged to `center` (model
    // space), plus the snap target (for the marker). Pointer-driven vertex
    // magnetism along ONE axis: if a neighbour vertex is under the cursor, slide
    // the cell along `axis` so its nearest face lands on that vertex's `axis`
    // coordinate. Only single-axis moves snap (arrow handles + the axis-locked
    // modal move) — a plane/centre drag (`axis` null) would otherwise jump the
    // cell out of its drag plane. No target under the cursor ⇒ grid-quantize.
    const computeMove = (
        cell: {id: string; origin: Vec3; size: Vec3},
        center: Vec3,
        axis: 0 | 1 | 2 | null,
    ): {origin: Vec3; target: Vec3 | null} => {
        const st = useCellBuilderStore.getState();
        const step = st.gridStep > 0 ? st.gridStep : 0.1;
        if (st.gizmoVertexSnap && axis !== null) {
            const target = nearestCornerToPointer(cell.id);
            if (target) {
                const rawOrigin: Vec3 = [
                    center[0] - cell.size[0] / 2,
                    center[1] - cell.size[1] / 2,
                    center[2] - cell.size[2] / 2,
                ];
                // Snap the near-or-far face depending on which the cursor is by:
                // pick whichever of the box's two faces along `axis` is closer to
                // the target's axis coordinate.
                const near = rawOrigin[axis]; // low face
                const far = rawOrigin[axis] + cell.size[axis]; // high face
                const alignLow = Math.abs(target[axis] - near) <= Math.abs(target[axis] - far);
                const origin: Vec3 = [...rawOrigin];
                origin[axis] = alignLow ? target[axis] : target[axis] - cell.size[axis];
                return {
                    origin: [quantize(origin[0], step), quantize(origin[1], step), quantize(origin[2], step)],
                    target,
                };
            }
        }
        return {origin: originFromCenter(center, cell.size, step), target: null};
    };

    const startModalMove = (cell: BuilderCell, axis: 0 | 1 | 2) => {
        // Switching axis/cell mid-grab: close the previous leg's undo step first
        // (keeping its position — a plain re-lock is a confirm, not a cancel).
        if (modalMove && modalMove.startT !== null) useCellBuilderStore.getState().endTransaction();
        const centerModel = cellCenterModel(cell);
        modalMove = {
            cellId: cell.id,
            axis,
            lineDir: new THREE.Vector3(axis === 0 ? 1 : 0, axis === 1 ? 1 : 0, axis === 2 ? 1 : 0),
            lineOrigin: new THREE.Vector3(centerModel[0], centerModel[1], centerModel[2]).add(offsetVec()),
            startT: null,
            startBox: {origin: [...cell.origin], size: [...cell.size]},
        };
        showGuideLine(axis, centerModel);
        renderer.domElement.style.cursor = "move";
    };

    const endModalMove = (cancel: boolean) => {
        if (!modalMove) return;
        const mm = modalMove;
        modalMove = null;
        if (mm.startT !== null) {
            if (cancel) {
                const st = useCellBuilderStore.getState();
                // Revert the cell — and any equipment that rode along — together.
                if (translateEquip.length)
                    st.moveCellAndEquipment(mm.cellId, [...mm.startBox.origin], translateEquip);
                else
                    st.updateCell(mm.cellId, {origin: [...mm.startBox.origin], size: [...mm.startBox.size]});
            }
            useCellBuilderStore.getState().endTransaction();
        }
        translateEquip = [];
        gizmo.enabled = true;
        guideLine.visible = false;
        snapMarker.visible = false;
        renderer.domElement.style.cursor = "";
        requestRender();
    };

    // Reconcile the modal-move with the store: active whenever the translate
    // gizmo is locked to an axis on a (non-loft) cell. Called at the end of
    // syncGizmo, so it can re-hide the gizmo helper that syncGizmo just showed.
    const reconcileModalMove = () => {
        const st = useCellBuilderStore.getState();
        const cell = st.selection ? st.cells[st.selection.cellId] : null;
        const on = !!(
            st.active &&
            st.gizmoMode === "translate" &&
            st.gizmoAxisLock !== null &&
            cell &&
            cell.kind !== "loft" &&
            st.cellsVisible
        );
        if (on && cell) {
            const axis = st.gizmoAxisLock as 0 | 1 | 2;
            if (!modalMove || modalMove.cellId !== cell.id || modalMove.axis !== axis) {
                startModalMove(cell, axis);
            }
            // Take over from the TransformControls widget: disable it and hide its
            // helper so a confirm-click can't grab a handle, and the pointer drives
            // the move instead.
            gizmo.enabled = false;
            gizmoHelper.visible = false;
        } else if (modalMove) {
            endModalMove(false);
        }
    };

    gizmo.addEventListener("dragging-changed", (e: any) => {
        const st = useCellBuilderStore.getState();
        if (controlsRef.current) controlsRef.current.enabled = !e.value;
        // Coalesce the whole widget drag into one undo step.
        if (e.value) {
            st.beginTransaction();
            // Seed the loft member-move baseline at the proxy's current (box-
            // centre) position; cleared when the drag ends.
            loftDragLast = gizmoProxy.position.clone();
            // Capture the equipment sitting in the cell so they ride along.
            const sel = st.selection;
            const cell = sel ? st.cells[sel.cellId] : null;
            translateEquip = cell && st.gizmoMode === "translate" ? equipContainedIn(cell) : [];
        } else {
            st.endTransaction();
            loftDragLast = null;
            translateEquip = [];
            showSnapMarker(null); // drag ended — clear the snap indicator
        }
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
            // Loft band: move the WHOLE member (not this one bay) by a grid-
            // quantized incremental delta. loftDragLast tracks the applied
            // position so the member steps in exact grid multiples with no drift.
            if (cell.kind === "loft" && cell.loft) {
                if (!loftDragLast) loftDragLast = gizmoProxy.position.clone();
                const dx = Math.round((gizmoProxy.position.x - loftDragLast.x) / step) * step;
                const dy = Math.round((gizmoProxy.position.y - loftDragLast.y) / step) * step;
                const dz = Math.round((gizmoProxy.position.z - loftDragLast.z) / step) * step;
                if (dx || dy || dz) {
                    st.moveLoftMember(cell.loft.member, [dx, dy, dz]);
                    loftDragLast.set(loftDragLast.x + dx, loftDragLast.y + dy, loftDragLast.z + dz);
                }
                return;
            }
            // Constrain the snap to the axis actually being dragged. A single-
            // axis handle (the X/Y/Z arrow) only moves the cell along that axis,
            // so a full-3D snap would essentially never fire (a corner can't come
            // within range in the other two axes). Match Blender: snap along the
            // drag axis. An explicit X/Y/Z lock wins; a plane/centre handle (no
            // single axis) falls back to full-3D snap.
            const handle = (gizmo as unknown as {axis: string | null}).axis;
            const handleAxis =
                handle === "X" ? 0 : handle === "Y" ? 1 : handle === "Z" ? 2 : null;
            const snapAxis = st.gizmoAxisLock ?? handleAxis;
            const {origin, target} = computeMove(
                cell,
                [gizmoProxy.position.x, gizmoProxy.position.y, gizmoProxy.position.z],
                snapAxis,
            );
            applyCellTranslate(cell, origin);
            showSnapMarker(target);
        } else if (st.gizmoMode === "rotate") {
            // Proxy euler is ZYX (see gizmoProxy.rotation.order) → matches the
            // store/compiler; snap to 0.1° so it reads cleanly in the panel.
            const e = gizmoProxy.rotation;
            const deg = (v: number) => Math.round(THREE.MathUtils.radToDeg(v) * 10) / 10;
            st.setCellRotation(cell.id, [deg(e.x), deg(e.y), deg(e.z)]);
        }
    });

    // --- Equipment-port edit gizmo -------------------------------------------
    // A dedicated TransformControls (separate from the cell gizmo so the two
    // never clobber each other) that edits ONE equipment port: translate moves
    // the nozzle position (snapping to the equipment bbox corners + any CAD
    // vertices), rotate spins the outward direction about the port ANCHOR (the
    // arrow root). The proxy lives in the container so it shares the model
    // offset — its position is model-space, like gizmoProxy.
    const portProxy = new THREE.Object3D();
    portProxy.userData.__excludeFromFit = true;
    container.add(portProxy);
    const portGizmo = new TransformControls(cameraRef.current ?? (camera as THREE.Camera), renderer.domElement);
    portGizmo.setSpace("world");
    const portGizmoHelper = portGizmo.getHelper();
    portGizmoHelper.userData.__excludeFromFit = true;
    portGizmoHelper.visible = false;
    scene.add(portGizmoHelper);
    // Outward world direction of the edited port at rotate-drag start; the
    // rotate delta accumulates from identity onto it (see objectChange).
    let portRotateStartDir: THREE.Vector3 | null = null;

    // Resolve an equipment port's anchor (nozzle) + outward direction in MODEL
    // space, mirroring rebuildPorts' math (including the equipment's ZYX spin).
    const portGeom = (
        cellId: string,
        portName: string,
    ): {anchor: Vec3; dir: Vec3; center: Vec3; quat: THREE.Quaternion | null} | null => {
        const st = useCellBuilderStore.getState();
        const cell = st.cells[cellId];
        if (!cell || cell.kind !== "equipment") return null;
        const p = portsForEquipment(cell, st.equipmentTypes).find((x) => x.name === portName);
        if (!p) return null;
        const cx = cell.origin[0] + cell.size[0] / 2;
        const cy = cell.origin[1] + cell.size[1] / 2;
        const cz = cell.origin[2];
        const rot = cell.rotation;
        const quat =
            rot && (rot[0] || rot[1] || rot[2])
                ? new THREE.Quaternion().setFromEuler(
                      new THREE.Euler(
                          THREE.MathUtils.degToRad(rot[0]),
                          THREE.MathUtils.degToRad(rot[1]),
                          THREE.MathUtils.degToRad(rot[2]),
                          "ZYX",
                      ),
                  )
                : null;
        const pos = p.position ?? [0, 0, 0];
        const lp = new THREE.Vector3(pos[0], pos[1], pos[2]);
        if (quat) lp.applyQuaternion(quat);
        const dv = p.direction_vector ?? [0, 0, 1];
        const d = new THREE.Vector3(dv[0], dv[1], dv[2]);
        if (quat) d.applyQuaternion(quat);
        if (d.lengthSq() < 1e-9) d.set(0, 0, 1);
        d.normalize();
        return {
            anchor: [cx + lp.x, cy + lp.y, cz + lp.z],
            dir: [d.x, d.y, d.z],
            center: [cx, cy, cz],
            quat,
        };
    };

    // CAD-mesh vertices (model space) for a CAD-backed equipment, so a port can
    // snap onto the real geometry — not just the bounding box. Best-effort:
    // active only when "Use CAD models" is on and a loaded mesh in the scene is
    // named for this equipment; downsampled so a dense asset can't stall the
    // per-move snap search. Boxes-only topology view yields none (bbox corners
    // remain the snap set).
    // The loaded scene mesh whose name matches this equipment cell (the CAD the
    // compiler spliced in), or null. The compiler emits one mesh per equipment
    // named for the cell (see collectCadVerts / _cad_transform).
    const findCadMesh = (cell: BuilderCell): THREE.Mesh | null => {
        let mesh: THREE.Mesh | null = null;
        (sceneRef.current ?? scene).traverse((o) => {
            if (mesh) return;
            if ((o as THREE.Mesh).isMesh && o.name && o.name === cell.name) mesh = o as THREE.Mesh;
        });
        return mesh;
    };

    // The model-space AABB of the CAD mesh loaded for this equipment cell, or
    // null when "Use CAD models" is off / no matching mesh is in the scene.
    // Rotation is already baked into the compiled CAD, so this axis-aligned box
    // wraps the placed (rotated) geometry directly. offsetVec() unmaps the
    // viewer's model translation so the result is in the same cell-origin frame
    // as cell.origin/size.
    const cadBoundsForCell = (cell: BuilderCell): {min: Vec3; max: Vec3} | null => {
        const st = useCellBuilderStore.getState();
        if (cell.kind !== "equipment" || !st.equipmentCad) return null;
        const mesh = findCadMesh(cell);
        if (!mesh) return null;
        // Refresh the world matrix chain so a just-loaded mesh reports correct
        // bounds (expandByObject only refreshes the object's own matrix).
        mesh.updateWorldMatrix(true, false);
        const b = new THREE.Box3().setFromObject(mesh, true);
        if (b.isEmpty()) return null;
        const off = offsetVec();
        return {
            min: [b.min.x - off.x, b.min.y - off.y, b.min.z - off.z],
            max: [b.max.x - off.x, b.max.y - off.y, b.max.z - off.z],
        };
    };

    // The box to draw + interact with for a cell: an equipment's CAD-fitted AABB
    // when a linked CAD mesh is loaded, else the declared LX/LY/LZ box. `cadFitted`
    // flags the CAD case so callers can skip re-applying the cell rotation (it is
    // already baked into the CAD bounds).
    const displayBoxForCell = (cell: BuilderCell): {box: CellBox; cadFitted: boolean} => {
        const bounds = cadBoundsForCell(cell);
        return {box: cadDisplayBox(cell, bounds), cadFitted: bounds !== null};
    };

    const collectCadVerts = (cell: BuilderCell): Vec3[] => {
        const st = useCellBuilderStore.getState();
        if (!st.equipmentCad) return [];
        const mesh = findCadMesh(cell);
        if (!mesh) return [];
        const foundMesh: THREE.Mesh = mesh;
        const posAttr = (foundMesh.geometry as THREE.BufferGeometry).getAttribute("position");
        if (!posAttr) return [];
        const off = offsetVec();
        const MAX = 4000;
        const stride = Math.max(1, Math.floor(posAttr.count / MAX));
        const v = new THREE.Vector3();
        const out: Vec3[] = [];
        for (let i = 0; i < posAttr.count; i += stride) {
            v.fromBufferAttribute(posAttr, i).applyMatrix4(foundMesh.matrixWorld);
            out.push([v.x - off.x, v.y - off.y, v.z - off.z]);
        }
        return out;
    };

    // The port snap target (bbox corner or CAD vertex, model space) nearest the
    // pointer on screen within SNAP_PX, or null — the port analogue of
    // nearestCornerToPointer.
    const nearestPortSnapToPointer = (cell: BuilderCell): Vec3 | null => {
        const cam = cameraRef.current ?? (camera as THREE.PerspectiveCamera);
        const off = offsetVec();
        const rect = renderer.domElement.getBoundingClientRect();
        const sx0 = (pointer.x * 0.5 + 0.5) * rect.width;
        const sy0 = (-pointer.y * 0.5 + 0.5) * rect.height;
        const v = new THREE.Vector3();
        let best: Vec3 | null = null;
        let bestD = SNAP_PX;
        for (const t of portSnapTargets(displayBoxForCell(cell).box, collectCadVerts(cell))) {
            v.set(t[0] + off.x, t[1] + off.y, t[2] + off.z).project(cam);
            if (v.z < -1 || v.z > 1) continue;
            const sx = (v.x * 0.5 + 0.5) * rect.width;
            const sy = (-v.y * 0.5 + 0.5) * rect.height;
            const d = Math.hypot(sx - sx0, sy - sy0);
            if (d <= bestD) {
                bestD = d;
                best = t;
            }
        }
        return best;
    };

    // A dedicated raycaster restricted to layer 1 (where the port glyphs live),
    // so a right-click can hit a port arrow/marker that the normal pick ignores.
    const portRaycaster = new THREE.Raycaster();
    portRaycaster.layers.set(1);

    const pickPort = (): {cellId: string; portName: string} | null => {
        const st = useCellBuilderStore.getState();
        if (!st.active || !st.portsOverlayVisible) return null;
        portRaycaster.setFromCamera(pointer, cameraRef.current ?? (camera as THREE.Camera));
        const hits = portRaycaster.intersectObjects(portsGroup.children, true);
        for (const h of hits) {
            let o: THREE.Object3D | null = h.object;
            while (o) {
                const cid = o.userData.__portCellId as string | undefined;
                const pn = o.userData.__portName as string | undefined;
                if (cid && pn) return {cellId: cid, portName: pn};
                o = o.parent;
            }
        }
        return null;
    };

    const syncPortGizmo = () => {
        const st = useCellBuilderStore.getState();
        if (cameraRef.current) portGizmo.camera = cameraRef.current;
        const pg = st.portGizmo;
        const info = pg ? portGeom(pg.cellId, pg.portName) : null;
        const on = !!(st.active && pg && info && st.portsOverlayVisible && st.cellsVisible);
        if (on && pg && info) {
            if (!portGizmo.dragging) {
                portProxy.rotation.set(0, 0, 0);
                portProxy.position.set(info.anchor[0], info.anchor[1], info.anchor[2]);
            }
            if (portGizmo.object !== portProxy) portGizmo.attach(portProxy);
            if (pg.mode === "rotate") {
                portGizmo.setMode("rotate");
                portGizmo.setRotationSnap(THREE.MathUtils.degToRad(15));
            } else {
                portGizmo.setMode("translate");
                portGizmo.setTranslationSnap(
                    st.gizmoVertexSnap ? null : st.gridStep > 0 ? st.gridStep : null,
                );
            }
            portGizmoHelper.visible = true;
        } else {
            if (portGizmo.object) portGizmo.detach();
            portGizmoHelper.visible = false;
        }
        requestRender();
    };

    portGizmo.addEventListener("dragging-changed", (e: any) => {
        const st = useCellBuilderStore.getState();
        if (controlsRef.current) controlsRef.current.enabled = !e.value;
        if (e.value) {
            st.beginTransaction();
            const pg = st.portGizmo;
            const info = pg ? portGeom(pg.cellId, pg.portName) : null;
            portRotateStartDir = info ? new THREE.Vector3(info.dir[0], info.dir[1], info.dir[2]) : null;
            portProxy.rotation.set(0, 0, 0); // rotate delta accumulates from identity
        } else {
            st.endTransaction();
            portRotateStartDir = null;
            showSnapMarker(null);
        }
        requestRender();
    });

    portGizmo.addEventListener("objectChange", () => {
        const st = useCellBuilderStore.getState();
        const pg = st.portGizmo;
        if (!pg) return;
        const cell = st.cells[pg.cellId];
        const info = portGeom(pg.cellId, pg.portName);
        if (!cell || !info) return;
        const invQuat = info.quat ? info.quat.clone().invert() : null;
        if (pg.mode === "translate") {
            // Proxy position is the dragged (model-space) nozzle; vertex snap
            // pulls it onto the nearest bbox corner / CAD vertex under the cursor.
            let nozzle: Vec3 = [portProxy.position.x, portProxy.position.y, portProxy.position.z];
            let target: Vec3 | null = null;
            if (st.gizmoVertexSnap) {
                target = nearestPortSnapToPointer(cell);
                if (target) nozzle = target;
            }
            // Back out the equipment's footprint centre + spin to the port's
            // stored LOCAL position (so it round-trips like the type geometry).
            const local = new THREE.Vector3(
                nozzle[0] - info.center[0],
                nozzle[1] - info.center[1],
                nozzle[2] - info.center[2],
            );
            if (invQuat) local.applyQuaternion(invQuat);
            st.updateEquipmentPort(pg.cellId, pg.portName, {position: [local.x, local.y, local.z]});
            showSnapMarker(target);
        } else {
            // Rotate about the anchor: apply the proxy's accumulated rotation to
            // the start direction, then back out the equipment spin to store the
            // port's LOCAL outward direction. Position is unchanged.
            if (!portRotateStartDir) return;
            const world = portRotateStartDir.clone().applyQuaternion(portProxy.quaternion).normalize();
            if (invQuat) world.applyQuaternion(invQuat);
            world.normalize();
            st.updateEquipmentPort(pg.cellId, pg.portName, {
                direction_vector: [world.x, world.y, world.z],
            });
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
        // Translate works for every kind — including a loft band, whose gizmo
        // moves the whole member (see the loft branch in objectChange). The
        // proxy still seeds from the band's bounding-box centre below.
        const translateOn = !!(st.active && st.gizmoMode === "translate" && cell && st.cellsVisible);
        // Rotate is equipment-only — spaces stay axis-aligned lattice boxes.
        const rotateOn = !!(
            st.active && st.gizmoMode === "rotate" && cell && cell.kind === "equipment" && st.cellsVisible
        );
        if (translateOn && cell) {
            // With vertex magnetism on, let the widget move continuously so the
            // snap in objectChange (corner-to-corner, or axis-locked) fully owns
            // where the cell lands. Otherwise fall back to the grid step so the
            // gizmo itself steps on the grid.
            gizmo.setTranslationSnap(
                st.gizmoVertexSnap ? null : st.gridStep > 0 ? st.gridStep : null,
            );
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
        // After the widget visibility is set, let the axis-locked modal move take
        // over (it re-hides the helper it doesn't need).
        reconcileModalMove();
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
        longPressStartX = ev.clientX;
        longPressStartY = ev.clientY;
        const {clientX, clientY} = ev;
        // A long-press ON A PORT ARROW opens the port Move/Rotate menu (the touch
        // equivalent of the desktop right-click, which orbit-controls otherwise
        // swallow as a camera drag). Ports win over the cell body behind them.
        const hitPort = pickPort();
        if (hitPort) {
            longPressTimer = setTimeout(() => {
                longPressTimer = null;
                drag = null;
                pendingSelect = null;
                useCellBuilderStore
                    .getState()
                    .openPortMenu(clientX, clientY, hitPort.cellId, hitPort.portName);
            }, LONG_PRESS_MS);
            return;
        }
        const hit = pickBuilderMesh();
        if (!hit) return;
        const cellId = hit.object.userData.__cellId as string;
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
        const size = addModeSize(st);
        // Place on top of a hovered cell, else on the model's ground plane.
        const hit = pickBuilderMesh();
        let base: Vec3 | null = null;
        // Ground level (model Z) for empty-space placement: the existing cells'
        // lowest floor, NOT model z=0. A project template is centred far from the
        // origin (e.g. greenvolt sits ~498 m up, translated back to the middle of
        // the scene), so a z=0 plane would be way below the visible model and the
        // new cell would land off-screen — which reads as "+ Cell does nothing".
        const existing = Object.values(st.cells);
        const groundZ = existing.length ? Math.min(...existing.map((c) => c.origin[2])) : 0;
        let z = groundZ;
        if (hit) {
            const cellId = hit.object.userData.__cellId as string;
            const cell = st.cells[cellId];
            base = worldToModel(hit.point);
            z = cell ? cell.origin[2] + cell.size[2] : base[2];
        } else {
            // Plane at world z = groundZ + offset (i.e. model z = groundZ).
            const planeWorldZ = groundZ + offsetVec().z;
            const groundPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -planeWorldZ);
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
        // Red for an opening (a cut), green for an additive cell/equipment.
        (ghost.material as THREE.MeshBasicMaterial).color.setHex(
            st.mode === "add-opening" ? OPENING_COLOR : GHOST_COLOR,
        );
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

        // Axis-locked modal move active: a left-click confirms the placement and
        // drops the axis lock (back to the 3-axis gizmo). No handle grab needed.
        if (modalMove) {
            endModalMove(false);
            st.setGizmoAxisLock(null);
            ev.stopPropagation();
            return;
        }

        if (
            st.mode === "add-cell" ||
            st.mode === "add-equipment" ||
            st.mode === "add-opening"
        ) {
            if (placeEntry) {
                // Numeric placement is driving the ghost — a click doesn't place.
                ev.stopPropagation();
                return;
            }
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

        // Axis-locked modal move: the cell tracks the pointer along the locked
        // axis, no button held. First move seeds the reference (no jump) and opens
        // one undo step; later moves slide the cell (with vertex snapping).
        if (modalMove && st.gizmoMode === "translate" && st.gizmoAxisLock === modalMove.axis) {
            const cell = st.cells[modalMove.cellId];
            if (!cell) {
                endModalMove(false);
                return;
            }
            const t = lineParamFromRay(raycaster.ray, modalMove.lineOrigin, modalMove.lineDir);
            if (t !== null) {
                if (modalMove.startT === null) {
                    modalMove.startT = t;
                    st.beginTransaction();
                    // Capture the equipment riding along with this cell.
                    translateEquip = equipContainedIn(cell);
                } else {
                    const axis = modalMove.axis;
                    const center = cellCenterModel(cell);
                    const startCenterAxis = modalMove.startBox.origin[axis] + modalMove.startBox.size[axis] / 2;
                    center[axis] = startCenterAxis + (t - modalMove.startT);
                    const {origin, target} = computeMove(cell, center, axis);
                    applyCellTranslate(cell, origin);
                    showSnapMarker(target);
                }
            }
            ev.stopPropagation();
            return;
        }

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
            if (!placeEntry) updateGhost(); // numeric placement owns the ghost when active
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
        // A right-click ON a port arrow opens the port edit menu (Move / Rotate)
        // — checked before the cell menu so a port glyph over a cell wins.
        const port = pickPort();
        if (port) {
            ev.preventDefault();
            st.openPortMenu(ev.clientX, ev.clientY, port.cellId, port.portName);
            return;
        }
        const hit = pickBuilderMesh();
        if (!hit) return;
        ev.preventDefault();
        const cellId = hit.object.userData.__cellId as string;
        st.openContextMenu(ev.clientX, ev.clientY, cellId);
    };

    // --- Keyboard interactive extrude + numeric entry -------------------------
    // A live preview (the reused green `ghost` mesh + an on-canvas readout)
    // driven purely from the keyboard: E starts it, digits/`.`/`-` type the
    // depth, Enter commits (store mutates only here), Esc cancels. Three shapes:
    // extruding a box cell from its selected face, extending a loft stack up its
    // spine, and resizing a loft station's section — all share the typing UX.
    type NumEntry =
        | {
              kind: "cellExtrude";
              cellId: string;
              faceIndex: number;
              axis: 0 | 1 | 2;
              defaultDepth: number;
              typed: string;
          }
        | {
              kind: "loftExtend";
              memberName: string;
              defaultSpacing: number;
              anchor: Vec3;
              typed: string;
          }
        | {
              kind: "loftResize";
              memberName: string;
              stationIndex: number;
              section: "rectangle" | "circle";
              defaultVal: number;
              anchor: Vec3;
              typed: string;
          };
    let numEntry: NumEntry | null = null;

    // Active loft station for keyboard station edits (S/T) — decoupled from the
    // bay selection so E's freshly-added top station and L's base station are
    // both directly addressable. Reset when the selected member changes.
    let loftActive: {member: string; index: number} | null = null;

    // On-canvas numeric readout (a screen-space sprite, like snapMarker).
    const readoutCanvas = document.createElement("canvas");
    readoutCanvas.width = 256;
    readoutCanvas.height = 64;
    const readoutTex = new THREE.CanvasTexture(readoutCanvas);
    const readout = new THREE.Sprite(
        new THREE.SpriteMaterial({map: readoutTex, depthTest: false, transparent: true, sizeAttenuation: false}),
    );
    readout.scale.set(0.17, 0.043, 1);
    readout.renderOrder = 7;
    readout.userData.__excludeFromFit = true;
    readout.visible = false;
    scene.add(readout);
    const drawReadout = (text: string) => {
        const g = readoutCanvas.getContext("2d")!;
        g.clearRect(0, 0, 256, 64);
        g.fillStyle = "rgba(17,24,39,0.86)";
        const r = 12;
        g.beginPath();
        g.moveTo(r, 2);
        g.arcTo(254, 2, 254, 62, r);
        g.arcTo(254, 62, 2, 62, r);
        g.arcTo(2, 62, 2, 2, r);
        g.arcTo(2, 2, 254, 2, r);
        g.fill();
        g.fillStyle = "#22c55e";
        g.font = "bold 34px ui-monospace, monospace";
        g.textAlign = "center";
        g.textBaseline = "middle";
        g.fillText(text, 128, 34);
        readoutTex.needsUpdate = true;
    };
    const showReadout = (text: string, modelPos: Vec3) => {
        drawReadout(text);
        const off = offsetVec();
        readout.position.set(modelPos[0] + off.x, modelPos[1] + off.y, modelPos[2] + off.z);
        readout.visible = true;
    };
    const hideReadout = () => {
        if (readout.visible) readout.visible = false;
    };
    const fmt = (v: number): string => `${Math.round(v * 1000) / 1000}`;

    // Live ring outline for a loft section resize — a green LineLoop drawn at the
    // station's NEW dimensions so the ring visibly scales as you type. Model-space
    // (added to the container, which carries the model offset), like the ghost.
    const ringPreview = new THREE.LineLoop(
        new THREE.BufferGeometry(),
        new THREE.LineBasicMaterial({color: GHOST_COLOR, depthTest: false, transparent: true, opacity: 0.95}),
    );
    ringPreview.userData.__excludeFromFit = true;
    ringPreview.renderOrder = 6;
    ringPreview.visible = false;
    container.add(ringPreview);
    const showRingPreview = (pts: Vec3[]) => {
        if (pts.length < 2) {
            ringPreview.visible = false;
            return;
        }
        const arr = new Float32Array(pts.length * 3);
        for (let i = 0; i < pts.length; i++) {
            arr[i * 3] = pts[i][0];
            arr[i * 3 + 1] = pts[i][1];
            arr[i * 3 + 2] = pts[i][2];
        }
        ringPreview.geometry.setAttribute("position", new THREE.BufferAttribute(arr, 3));
        ringPreview.geometry.computeBoundingSphere();
        ringPreview.visible = true;
    };
    const hideRingPreview = () => {
        if (ringPreview.visible) ringPreview.visible = false;
    };

    // Parse the typed buffer to a number, falling back to `def` for the empty /
    // partial ("", "-", ".") states; a lone "-" flips the default's sign.
    const parseTyped = (typed: string, def: number): number => {
        if (typed === "" || typed === "." || typed === "-.") return def;
        if (typed === "-") return -def;
        const v = Number(typed);
        return Number.isFinite(v) ? v : def;
    };

    const loftMemberByName = (name: string) =>
        useCellBuilderStore.getState().loftMembers.find((m) => m.NAME === name) ?? null;

    // Bounds of two rings (for the loft-extend ghost box).
    const ringsBounds = (lo: Vec3[], hi: Vec3[]): CellBox => {
        const min: Vec3 = [Infinity, Infinity, Infinity];
        const max: Vec3 = [-Infinity, -Infinity, -Infinity];
        for (const p of [...lo, ...hi]) {
            for (let a = 0; a < 3; a++) {
                if (p[a] < min[a]) min[a] = p[a];
                if (p[a] > max[a]) max[a] = p[a];
            }
        }
        return {origin: min, size: [max[0] - min[0], max[1] - min[1], max[2] - min[2]]};
    };

    const setGhostBox = (box: CellBox, color: number = GHOST_COLOR) => {
        // Reuses the single GHOST box mesh (also used by add-mode placement; the
        // modes never overlap). `color` tints it per use — green for additive
        // (cell / extrude / loft), red for an opening (a negative-volume cut).
        (ghost.material as THREE.MeshBasicMaterial).color.setHex(color);
        ghost.scale.set(Math.max(box.size[0], 1e-3), Math.max(box.size[1], 1e-3), Math.max(box.size[2], 1e-3));
        ghost.position.set(
            box.origin[0] + box.size[0] / 2,
            box.origin[1] + box.size[1] / 2,
            box.origin[2] + box.size[2] / 2,
        );
        ghost.visible = true;
    };

    const refreshNumEntry = () => {
        if (!numEntry) return;
        const st = useCellBuilderStore.getState();
        if (numEntry.kind === "cellExtrude") {
            const cell = st.cells[numEntry.cellId];
            if (!cell) return endNumEntry(true);
            const v = parseTyped(numEntry.typed, numEntry.defaultDepth);
            const box = extrudeBox(cell, numEntry.faceIndex, v);
            if (Math.abs(box.size[numEntry.axis]) < 1e-6) ghost.visible = false;
            else setGhostBox(box);
            showReadout(`${fmt(v)} m`, faceCenter(cell, numEntry.faceIndex));
            st.setToolHint(`Extrude ${fmt(v)} m — type depth, ↵ commit, Esc cancel`);
        } else if (numEntry.kind === "loftExtend") {
            const member = loftMemberByName(numEntry.memberName);
            if (!member || !member.STATIONS.length) return endNumEntry(true);
            const v = parseTyped(numEntry.typed, numEntry.defaultSpacing);
            const top = member.STATIONS[member.STATIONS.length - 1];
            const lo = stationRingPoints(top, member.PLACEMENT);
            const hi = stationRingPoints({...top, Z: Number(top.Z) + v}, member.PLACEMENT);
            const box = ringsBounds(lo, hi);
            if (Math.abs(box.size[2]) < 1e-6) ghost.visible = false;
            else setGhostBox(box);
            showReadout(`↑ ${fmt(v)} m`, numEntry.anchor);
            st.setToolHint(`Loft +${fmt(v)} m — type spacing, ↵ commit, Esc cancel`);
        } else {
            const v = parseTyped(numEntry.typed, numEntry.defaultVal);
            ghost.visible = false; // the ring outline is the preview here
            // Redraw the station's ring at the new section size (circle → RADIUS,
            // rectangle → WIDTH=HEIGHT, matching resizeLoftStation) so it scales live.
            const member = loftMemberByName(numEntry.memberName);
            const station = member?.STATIONS?.[numEntry.stationIndex];
            if (member && station) {
                const resized =
                    numEntry.section === "circle"
                        ? {...station, RADIUS: v}
                        : {...station, WIDTH: v, HEIGHT: v};
                showRingPreview(stationRingPoints(resized, member.PLACEMENT));
            } else {
                hideRingPreview();
            }
            showReadout(`${numEntry.section === "circle" ? "r" : "□"} ${fmt(v)} m`, numEntry.anchor);
            st.setToolHint(
                `Section ${numEntry.section === "circle" ? "r" : "□"} ${fmt(v)} m — type size, ↵ commit, Esc cancel`,
            );
        }
        requestRender();
    };

    const commitNumEntry = () => {
        if (!numEntry) return;
        const st = useCellBuilderStore.getState();
        const entry = numEntry;
        if (entry.kind === "cellExtrude") {
            const v = parseTyped(entry.typed, entry.defaultDepth);
            if (Math.abs(v) > 1e-6) st.extendCellFromFace(entry.cellId, entry.faceIndex, v);
        } else if (entry.kind === "loftExtend") {
            const v = parseTyped(entry.typed, entry.defaultSpacing);
            if (Math.abs(v) > 1e-6) {
                st.extendLoftStack(entry.memberName, v);
                const m = loftMemberByName(entry.memberName);
                if (m) setLoftActive(entry.memberName, m.STATIONS.length - 1);
            }
        } else {
            const v = parseTyped(entry.typed, entry.defaultVal);
            st.resizeLoftStation(entry.memberName, entry.stationIndex, Math.max(0, v));
        }
        endNumEntry(false);
    };

    const endNumEntry = (_cancel: boolean) => {
        numEntry = null;
        ghost.visible = false;
        ghostBox = null;
        hideReadout();
        hideRingPreview();
        useCellBuilderStore.getState().setToolHint(null);
        requestRender();
    };

    // Numeric placement for add-cell / add-opening / add-equipment: type X, `,`
    // to the next axis, Y, `,`, Z, Enter to drop the box at exactly (x,y,z) —
    // no pointer needed. `.` is the decimal, `,` steps axes. Unentered axes
    // default to 0 / the model ground (z). Lazily created on the first digit
    // while in an add mode; the pointer ghost is suspended while it's live.
    let placeEntry: {axis: 0 | 1 | 2; vals: [number | null, number | null, number | null]; typed: string} | null =
        null;
    const placeOrigin = (pe: NonNullable<typeof placeEntry>): Vec3 => {
        const cells = Object.values(useCellBuilderStore.getState().cells);
        const groundZ = cells.length ? Math.min(...cells.map((c) => c.origin[2])) : 0;
        const def: Vec3 = [0, 0, groundZ];
        const val = (a: 0 | 1 | 2): number => {
            if (pe.vals[a] != null) return pe.vals[a] as number;
            if (a === pe.axis && pe.typed !== "") return parseTyped(pe.typed, def[a]);
            return def[a];
        };
        return [val(0), val(1), val(2)];
    };
    const refreshPlaceEntry = () => {
        if (!placeEntry) return;
        const st = useCellBuilderStore.getState();
        const size = addModeSize(st);
        const o = placeOrigin(placeEntry);
        const origin: Vec3 = [
            quantize(o[0], st.gridStep),
            quantize(o[1], st.gridStep),
            quantize(o[2], st.gridStep),
        ];
        setGhostBox({origin, size}, st.mode === "add-opening" ? OPENING_COLOR : GHOST_COLOR);
        const AX = ["x", "y", "z"];
        showReadout(
            `${AX[placeEntry.axis]} ${fmt(o[placeEntry.axis])}`,
            [origin[0] + size[0] / 2, origin[1] + size[1] / 2, origin[2] + size[2] / 2],
        );
        st.setToolHint(
            `Place @ (${fmt(o[0])}, ${fmt(o[1])}, ${fmt(o[2])}) — type, "," next axis, ↵ place, Esc cancel`,
        );
        requestRender();
    };
    const endPlaceEntry = () => {
        placeEntry = null;
        ghost.visible = false;
        ghostBox = null;
        hideReadout();
        useCellBuilderStore.getState().setToolHint(null);
        requestRender();
    };
    const commitPlaceEntry = () => {
        if (!placeEntry) return;
        const st = useCellBuilderStore.getState();
        if (placeEntry.typed !== "") placeEntry.vals[placeEntry.axis] = parseTyped(placeEntry.typed, 0);
        const size = addModeSize(st);
        const o = placeOrigin(placeEntry);
        const origin: Vec3 = [
            quantize(o[0], st.gridStep),
            quantize(o[1], st.gridStep),
            quantize(o[2], st.gridStep),
        ];
        const kind =
            st.mode === "add-opening" ? "opening" : st.mode === "add-equipment" ? "equipment" : "cell";
        st.addCell(kind, origin, size); // sets mode idle + selects the new cell
        endPlaceEntry();
    };

    // --- Keyboard equipment insert (I) ---------------------------------------
    // Two phases: "pick" chooses the equipment TYPE (T cycles) and the host CELL
    // (N/P cycle, highlighted via selection); Enter locks the host and moves to
    // "xy", where local (X,Y) in the cell frame are typed ("," steps X->Y),
    // Enter places on the cell floor. The green ghost previews the unit at the
    // current host + local position throughout.
    const spaceCells = (): BuilderCell[] =>
        Object.values(useCellBuilderStore.getState().cells)
            .filter((c) => c.kind === "cell")
            .sort((a, b) => a.name.localeCompare(b.name));

    let equipEntry: {
        phase: "pick" | "xy";
        hostId: string;
        axis: 0 | 1; // xy phase: 0=X, 1=Y (cell-local)
        vals: [number | null, number | null];
        typed: string;
    } | null = null;

    const equipTypeName = (): string => {
        const st = useCellBuilderStore.getState();
        const t = st.equipmentTypes.find((x) => x.slug === st.selectedEquipmentType);
        return t?.name ?? st.selectedEquipmentType ?? "EQ";
    };

    const refreshEquipEntry = () => {
        if (!equipEntry) return;
        const st = useCellBuilderStore.getState();
        const host = st.cells[equipEntry.hostId];
        if (!host) return endEquipEntry();
        const size = DEFAULT_EQUIPMENT_SIZE;
        const local: [number, number] =
            equipEntry.phase === "xy"
                ? [
                      equipEntry.vals[0] ??
                          (equipEntry.axis === 0 ? parseTyped(equipEntry.typed, host.size[0] / 2) : host.size[0] / 2),
                      equipEntry.vals[1] ??
                          (equipEntry.axis === 1 ? parseTyped(equipEntry.typed, host.size[1] / 2) : host.size[1] / 2),
                  ]
                : [host.size[0] / 2, host.size[1] / 2];
        // Cell-local (X,Y) centre -> world origin (min corner), seated on floor.
        const origin: Vec3 = [
            host.origin[0] + local[0] - size[0] / 2,
            host.origin[1] + local[1] - size[1] / 2,
            host.origin[2],
        ];
        setGhostBox({origin, size});
        const centre: Vec3 = [origin[0] + size[0] / 2, origin[1] + size[1] / 2, origin[2] + size[2] / 2];
        if (equipEntry.phase === "pick") {
            showReadout(`${equipTypeName()} → ${host.name}`, centre);
            st.setToolHint(
                `Insert ${equipTypeName()} @ cell ${host.name} — T type, N/P cell, ↵ pick, Esc cancel`,
            );
        } else {
            showReadout(`${equipTypeName()} (${fmt(local[0])}, ${fmt(local[1])})`, centre);
            st.setToolHint(
                `Equip ${equipTypeName()} @ cell ${host.name} local (${fmt(local[0])}, ${fmt(local[1])}) — type, "," X→Y, ↵ place, Esc cancel`,
            );
        }
        requestRender();
    };

    const endEquipEntry = () => {
        equipEntry = null;
        ghost.visible = false;
        ghostBox = null;
        hideReadout();
        useCellBuilderStore.getState().setToolHint(null);
        requestRender();
    };

    const startEquipInsert = (): boolean => {
        const st = useCellBuilderStore.getState();
        const cells = spaceCells();
        if (!cells.length) {
            st.setToolHint("Add a cell first — equipment needs a host cell");
            return false;
        }
        // Default the type if none is chosen yet, so the ghost/readout have one.
        if (!st.selectedEquipmentType && st.equipmentTypes.length) {
            st.setSelectedEquipmentType(st.equipmentTypes[0].slug);
        }
        const selId = st.selection?.cellId;
        const host = (selId && st.cells[selId]?.kind === "cell" ? st.cells[selId] : null) ?? cells[0];
        equipEntry = {phase: "pick", hostId: host.id, axis: 0, vals: [null, null], typed: ""};
        if (st.selection?.cellId !== host.id) st.setSelection({kind: "cell", cellId: host.id});
        refreshEquipEntry();
        return true;
    };

    const cycleEquipHost = (dir: 1 | -1) => {
        if (!equipEntry) return;
        const cells = spaceCells();
        if (!cells.length) return;
        const i = cells.findIndex((c) => c.id === equipEntry!.hostId);
        const host = cells[((i < 0 ? 0 : i) + dir + cells.length) % cells.length];
        equipEntry.hostId = host.id;
        const st = useCellBuilderStore.getState();
        if (st.selection?.cellId !== host.id) st.setSelection({kind: "cell", cellId: host.id});
        refreshEquipEntry();
    };

    // --- Keyboard opening-on-face insert (O) ---------------------------------
    // A cell FACE must be selected. Numeric fields in the face's 2D plane:
    // [X, Y] lower corner, then [W, H], then DEPTH (half through-thickness). ","
    // steps to the next field; Enter finishes the current stage (X/Y -> W/H ->
    // DEPTH) and commits on the last. The negative box straddles the face plane.
    const OPEN_FIELDS = ["x", "y", "w", "h", "depth"] as const;
    let openEntry: {
        cellId: string;
        faceIndex: number;
        field: 0 | 1 | 2 | 3 | 4;
        vals: [number, number, number, number, number]; // X, Y, W, H, DEPTH
        typed: string;
    } | null = null;

    const openVals = (oe: NonNullable<typeof openEntry>): [number, number, number, number, number] => {
        const out = [...oe.vals] as [number, number, number, number, number];
        if (oe.typed !== "") out[oe.field] = parseTyped(oe.typed, oe.vals[oe.field]);
        return out;
    };

    const refreshOpenEntry = () => {
        if (!openEntry) return;
        const st = useCellBuilderStore.getState();
        const cell = st.cells[openEntry.cellId];
        if (!cell || cell.kind !== "cell") return endOpenEntry();
        const [x, y, w, h, d] = openVals(openEntry);
        const box = openingBoxOnFace(cell, openEntry.faceIndex, x, y, w, h, d);
        setGhostBox(box, OPENING_COLOR); // red — a negative-volume cut
        const centre: Vec3 = [
            box.origin[0] + box.size[0] / 2,
            box.origin[1] + box.size[1] / 2,
            box.origin[2] + box.size[2] / 2,
        ];
        const fieldVal = [x, y, w, h, d][openEntry.field];
        showReadout(`${OPEN_FIELDS[openEntry.field]} ${fmt(fieldVal)}`, centre);
        st.setToolHint(
            `Opening ${OPEN_FIELDS[openEntry.field]}=${fmt(fieldVal)} (x${fmt(x)} y${fmt(y)} w${fmt(w)} h${fmt(h)} d${fmt(d)}) — type, "," next, ↵ next/commit, Esc cancel`,
        );
        requestRender();
    };

    const endOpenEntry = () => {
        openEntry = null;
        ghost.visible = false;
        ghostBox = null;
        hideReadout();
        useCellBuilderStore.getState().setToolHint(null);
        requestRender();
    };

    const startOpeningOnFace = (cell: BuilderCell, faceIndex: number): boolean => {
        if (cell.kind !== "cell" || !BOX_FACE_SIDES[faceIndex]) return false;
        openEntry = {cellId: cell.id, faceIndex, field: 0, vals: [0, 0, 1, 1, 1], typed: ""};
        refreshOpenEntry();
        return true;
    };

    const commitOpenEntry = () => {
        if (!openEntry) return;
        const st = useCellBuilderStore.getState();
        const cell = st.cells[openEntry.cellId];
        if (cell && cell.kind === "cell") {
            const [x, y, w, h, d] = openVals(openEntry);
            const box = openingBoxOnFace(cell, openEntry.faceIndex, x, y, w, h, d);
            if (box.size[0] > 0 && box.size[1] > 0 && box.size[2] > 0) {
                // addCell uses the current selectedOpeningType's subtype and
                // round-trips as a USE_GLOBAL_COORDS negative box (one undo step).
                st.addCell("opening", box.origin, box.size);
            }
        }
        endOpenEntry();
    };

    const startCellExtrude = (cell: BuilderCell, faceIndex: number): boolean => {
        const side = BOX_FACE_SIDES[faceIndex];
        if (!side || cell.kind !== "cell") return false;
        numEntry = {
            kind: "cellExtrude",
            cellId: cell.id,
            faceIndex,
            axis: side.axis,
            defaultDepth: cell.size[side.axis],
            typed: "",
        };
        refreshNumEntry();
        return true;
    };

    const startLoftExtend = (cell: BuilderCell): boolean => {
        if (cell.kind !== "loft" || !cell.loft) return false;
        const member = loftMemberByName(cell.loft.member);
        if (!member || !member.STATIONS.length) return false;
        const n = member.STATIONS.length;
        const top = member.STATIONS[n - 1];
        const prev = n >= 2 ? member.STATIONS[n - 2] : null;
        const spacing = prev ? Math.abs(Number(top.Z) - Number(prev.Z)) || 3 : 3;
        numEntry = {
            kind: "loftExtend",
            memberName: member.NAME,
            defaultSpacing: spacing,
            anchor: [Number(top.X), Number(top.Y), Number(top.Z)],
            typed: "",
        };
        refreshNumEntry();
        return true;
    };

    const startLoftResize = (cell: BuilderCell): boolean => {
        if (cell.kind !== "loft" || !cell.loft) return false;
        const member = loftMemberByName(cell.loft.member);
        if (!member) return false;
        const idx =
            loftActive && loftActive.member === member.NAME ? loftActive.index : cell.loft.bay;
        const station = member.STATIONS[idx];
        if (!station) return false;
        const section = station.TYPE;
        numEntry = {
            kind: "loftResize",
            memberName: member.NAME,
            stationIndex: idx,
            section,
            defaultVal: section === "circle" ? (station.RADIUS ?? 1) : (station.WIDTH ?? 1),
            anchor: [Number(station.X), Number(station.Y), Number(station.Z)],
            typed: "",
        };
        refreshNumEntry();
        return true;
    };

    // Point the active loft station at `index` (clamped) and select the bay that
    // contains it so the panel/highlight follow. Sets loftActive.member first so
    // the selection subscription doesn't reset the index we just chose.
    const setLoftActive = (member: string, index: number) => {
        const st = useCellBuilderStore.getState();
        const m = loftMemberByName(member);
        if (!m) return;
        const nStations = m.STATIONS.length;
        const idx = Math.min(Math.max(index, 0), nStations - 1);
        loftActive = {member, index: idx};
        const bay = Math.min(idx, nStations - 2);
        const band = Object.values(st.cells).find(
            (c) => c.kind === "loft" && c.loft?.member === member && c.loft?.bay === bay,
        );
        if (band && st.selection?.cellId !== band.id) {
            st.setSelection({kind: "cell", cellId: band.id});
        }
        requestRender();
    };

    const onKeyDown = (ev: KeyboardEvent) => {
        const st = useCellBuilderStore.getState();
        if (!st.active) return;

        // Undo / redo — but not while typing in a form field (let the field's
        // own text undo win there).
        const target = ev.target as HTMLElement | null;
        const inField = !!target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);

        // An interactive extrude/station preview is live: capture numeric entry
        // so digits mean depth (not cell-type), Enter commits, Esc cancels,
        // Backspace edits. Any other key cancels the preview and falls through.
        if (!inField && numEntry && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            if (ev.key === "Enter") {
                commitNumEntry();
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (ev.key === "Escape") {
                endNumEntry(true);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (ev.key === "Backspace") {
                numEntry.typed = numEntry.typed.slice(0, -1);
                refreshNumEntry();
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // Accept both the main row and the NUMPAD. ev.code (Numpad0..9 /
            // NumpadDecimal / NumpadSubtract) catches the numpad even with NumLock
            // off (where ev.key would be a nav key); the decimal also accepts ","
            // (numpad separator on Nordic layouts).
            const numpadDigit = /^Numpad([0-9])$/.exec(ev.code);
            if (numpadDigit || /^[0-9]$/.test(ev.key)) {
                numEntry.typed += numpadDigit ? numpadDigit[1] : ev.key;
                refreshNumEntry();
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (
                (ev.key === "." || ev.key === "," || ev.code === "NumpadDecimal") &&
                !numEntry.typed.includes(".")
            ) {
                numEntry.typed += ".";
                refreshNumEntry();
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if ((ev.key === "-" || ev.code === "NumpadSubtract") && numEntry.typed === "") {
                numEntry.typed = "-";
                refreshNumEntry();
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // Not a numeric-entry key — abandon the preview, then let the key
            // fall through to its normal handling below.
            endNumEntry(true);
        }

        // Numeric placement while in an add mode: type X, "," next axis, Y, ",",
        // Z, Enter to drop the box at exactly (x,y,z). Activates on the first
        // digit (pointer placement still works until then); "." decimal, ","
        // steps axes, Enter places, Esc cancels.
        const inAddMode =
            st.mode === "add-cell" || st.mode === "add-opening" || st.mode === "add-equipment";
        if (!inField && inAddMode && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            const npd = /^Numpad([0-9])$/.exec(ev.code);
            const isDigit = !!npd || /^[0-9]$/.test(ev.key);
            if (placeEntry) {
                if (ev.key === "Enter") {
                    commitPlaceEntry();
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (ev.key === "Escape") {
                    endPlaceEntry();
                    st.setMode("idle");
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (ev.key === "Backspace") {
                    placeEntry.typed = placeEntry.typed.slice(0, -1);
                    refreshPlaceEntry();
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (
                    (ev.key === "." || ev.code === "NumpadDecimal") &&
                    !placeEntry.typed.includes(".")
                ) {
                    placeEntry.typed += ".";
                    refreshPlaceEntry();
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (ev.key === ",") {
                    // Advance to the next axis (commit the typed value first).
                    if (placeEntry.typed !== "")
                        placeEntry.vals[placeEntry.axis] = parseTyped(placeEntry.typed, 0);
                    placeEntry.axis = Math.min(2, placeEntry.axis + 1) as 0 | 1 | 2;
                    placeEntry.typed = "";
                    refreshPlaceEntry();
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if ((ev.key === "-" || ev.code === "NumpadSubtract") && placeEntry.typed === "") {
                    placeEntry.typed = "-";
                    refreshPlaceEntry();
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (isDigit) {
                    placeEntry.typed += npd ? npd[1] : ev.key;
                    refreshPlaceEntry();
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
            } else if (isDigit) {
                placeEntry = {axis: 0, vals: [null, null, null], typed: npd ? npd[1] : ev.key};
                refreshPlaceEntry();
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
        }

        // Equipment-insert flow (keyboard): pick phase (T type / N,P cell / ↵
        // lock) then xy phase (numeric local X,Y / ↵ place). Captures its keys.
        if (!inField && equipEntry && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            const npd = /^Numpad([0-9])$/.exec(ev.code);
            const isDigit = !!npd || /^[0-9]$/.test(ev.key);
            const consume = () => {
                ev.preventDefault();
                ev.stopPropagation();
            };
            if (ev.key === "Escape") {
                endEquipEntry();
                consume();
                return;
            }
            if (equipEntry.phase === "pick") {
                if (ev.key === "Enter") {
                    equipEntry.phase = "xy";
                    equipEntry.axis = 0;
                    equipEntry.vals = [null, null];
                    equipEntry.typed = "";
                    refreshEquipEntry();
                    consume();
                    return;
                }
                const k = ev.key.toLowerCase();
                if (k === "t") {
                    st.cycleEquipmentType(1);
                    refreshEquipEntry();
                    consume();
                    return;
                }
                if (k === "n" || k === "p") {
                    cycleEquipHost(k === "n" ? 1 : -1);
                    consume();
                    return;
                }
                return; // swallow nothing else in pick phase
            }
            // xy phase — numeric local X,Y.
            if (ev.key === "Enter") {
                if (equipEntry.typed !== "")
                    equipEntry.vals[equipEntry.axis] = parseTyped(equipEntry.typed, 0);
                const host = st.cells[equipEntry.hostId];
                const local: [number, number] = [
                    equipEntry.vals[0] ?? (host ? host.size[0] / 2 : 0),
                    equipEntry.vals[1] ?? (host ? host.size[1] / 2 : 0),
                ];
                const hostId = equipEntry.hostId;
                endEquipEntry();
                st.insertEquipmentAtLocal(hostId, local);
                consume();
                return;
            }
            if (ev.key === "Backspace") {
                equipEntry.typed = equipEntry.typed.slice(0, -1);
                refreshEquipEntry();
                consume();
                return;
            }
            if (ev.key === "," && equipEntry.axis === 0) {
                if (equipEntry.typed !== "")
                    equipEntry.vals[0] = parseTyped(equipEntry.typed, 0);
                equipEntry.axis = 1;
                equipEntry.typed = "";
                refreshEquipEntry();
                consume();
                return;
            }
            if ((ev.key === "." || ev.code === "NumpadDecimal") && !equipEntry.typed.includes(".")) {
                equipEntry.typed += ".";
                refreshEquipEntry();
                consume();
                return;
            }
            if ((ev.key === "-" || ev.code === "NumpadSubtract") && equipEntry.typed === "") {
                equipEntry.typed = "-";
                refreshEquipEntry();
                consume();
                return;
            }
            if (isDigit) {
                equipEntry.typed += npd ? npd[1] : ev.key;
                refreshEquipEntry();
                consume();
                return;
            }
            return;
        }

        // Opening-on-face flow (keyboard): numeric X,Y,W,H,DEPTH fields. Captures
        // its keys; "," steps a field, Enter finishes the stage / commits.
        if (!inField && openEntry && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            const npd = /^Numpad([0-9])$/.exec(ev.code);
            const isDigit = !!npd || /^[0-9]$/.test(ev.key);
            const consume = () => {
                ev.preventDefault();
                ev.stopPropagation();
            };
            if (ev.key === "Escape") {
                endOpenEntry();
                consume();
                return;
            }
            if (ev.key === "Enter") {
                if (openEntry.typed !== "")
                    openEntry.vals[openEntry.field] = parseTyped(openEntry.typed, openEntry.vals[openEntry.field]);
                if (openEntry.field >= 4) {
                    commitOpenEntry();
                } else {
                    // X/Y -> W (field 2); W/H -> DEPTH (field 4).
                    openEntry.field = (openEntry.field < 2 ? 2 : 4) as 0 | 1 | 2 | 3 | 4;
                    openEntry.typed = "";
                    refreshOpenEntry();
                }
                consume();
                return;
            }
            if (ev.key === "Backspace") {
                openEntry.typed = openEntry.typed.slice(0, -1);
                refreshOpenEntry();
                consume();
                return;
            }
            if (ev.key === ",") {
                if (openEntry.typed !== "")
                    openEntry.vals[openEntry.field] = parseTyped(openEntry.typed, openEntry.vals[openEntry.field]);
                openEntry.field = Math.min(4, openEntry.field + 1) as 0 | 1 | 2 | 3 | 4;
                openEntry.typed = "";
                refreshOpenEntry();
                consume();
                return;
            }
            if ((ev.key === "." || ev.code === "NumpadDecimal") && !openEntry.typed.includes(".")) {
                openEntry.typed += ".";
                refreshOpenEntry();
                consume();
                return;
            }
            if ((ev.key === "-" || ev.code === "NumpadSubtract") && openEntry.typed === "") {
                openEntry.typed = "-";
                refreshOpenEntry();
                consume();
                return;
            }
            if (isDigit) {
                openEntry.typed += npd ? npd[1] : ev.key;
                refreshOpenEntry();
                consume();
                return;
            }
            return;
        }

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

        // Delete / Backspace removes the selected cell(s)/equipment — the keyboard
        // equivalent of the context-menu Delete. Multi-selection deletes all in one
        // undo step. A loft bay: shrink the member by one station, or remove the
        // whole member when it's down to its last bay (2 stations) — so Del peels
        // bays and finally deletes the loft.
        if (!inField && (ev.key === "Delete" || ev.key === "Backspace")) {
            const ids = st.selectedCellIds.length
                ? st.selectedCellIds
                : st.selection
                  ? [st.selection.cellId]
                  : [];
            if (!ids.length) return;
            ev.preventDefault();
            ev.stopPropagation();
            st.beginTransaction();
            for (const id of ids) {
                const cur = useCellBuilderStore.getState();
                const cell = cur.cells[id];
                if (!cell) continue;
                if (cell.kind === "loft") {
                    const member = cur.loftMembers.find((m) => m.NAME === cell.loft?.member);
                    if (!member) continue;
                    if ((member.STATIONS?.length ?? 0) <= 2) {
                        cur.removeLoftMember(member.NAME);
                    } else {
                        const idx =
                            loftActive && loftActive.member === member.NAME
                                ? loftActive.index
                                : (cell.loft?.bay ?? 0);
                        cur.removeLoftStation(member.NAME, Math.min(idx, member.STATIONS.length - 1));
                    }
                } else {
                    cur.removeCell(id);
                }
            }
            st.endTransaction();
            requestRender();
            return;
        }

        // --- Blender-style gizmo shortcuts (not while typing in a field) ------
        // These consume the key (stopPropagation) so the global viewer handler
        // — same phase, but this listener runs in capture — doesn't also fire.
        if (!inField && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            const k = ev.key.toLowerCase();
            const cell = st.selection ? st.cells[st.selection.cellId] : null;
            // Representation-mode shortcuts (work regardless of selection so you
            // can flip views mid-edit), mirroring the Representation button row:
            // backtick cycles topology→simulation→detail, Shift+backtick reverses;
            // Shift+1/2/3 jump straight to a mode. Plain digits are left to the
            // builder's "1–9 = cell type" picker below (Shift+digit is `!@#`, which
            // that picker's /^[1-9]$/ test ignores — so no clash).
            const REP_MODES = ["topology", "simulation", "detail"] as const;
            if (ev.code === "Backquote") {
                const cur = Math.max(0, REP_MODES.indexOf(st.repMode));
                const dir = ev.shiftKey ? -1 : 1;
                const next = REP_MODES[(cur + dir + REP_MODES.length) % REP_MODES.length];
                void st.setRepMode(next);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (
                ev.shiftKey &&
                (ev.code === "Digit1" || ev.code === "Digit2" || ev.code === "Digit3")
            ) {
                void st.setRepMode(REP_MODES[Number(ev.code.slice(-1)) - 1]);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
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
            // Shift+X toggles exclusion on the selected loft face panel (drops
            // its plate on recompile). Maps the picked material index to the
            // member-relative face id via bandFaceIds.
            if (ev.shiftKey && k === "x") {
                const sel = st.selection;
                if (
                    sel?.kind === "face" &&
                    sel.faceIndex != null &&
                    cell?.kind === "loft" &&
                    cell.loft
                ) {
                    const {edges, caps} = bandFaceIds(cell.loft);
                    const faceIds = [...edges, caps[0], caps[1]];
                    const fid = faceIds[sel.faceIndex];
                    if (fid) {
                        const excluded = cell.loft.excludeFaces.includes(fid);
                        st.setLoftFaceExcluded(cell.loft.member, fid, !excluded);
                        ev.preventDefault();
                        ev.stopPropagation();
                    }
                }
                return;
            }
            // Loft members: G moves the whole member (existing translate gizmo),
            // S starts a numeric section resize of the active station.
            if (!ev.shiftKey && cell && cell.kind === "loft") {
                if (k === "g") {
                    st.setGizmoMode("translate");
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                if (k === "s") {
                    if (startLoftResize(cell)) {
                        ev.preventDefault();
                        ev.stopPropagation();
                    }
                    return;
                }
            }
            // G/R/S activate the translate / rotate / resize gizmo (Blender keys):
            // rotate is equipment-only, resize is cell-only (matches the menus).
            if (!ev.shiftKey && cell && cell.kind !== "loft") {
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

            // --- Keyboard topology scheme (single keys) ----------------------
            if (!ev.shiftKey) {
                // L: start a new loft member. With a cell FACE selected, base the
                // loft on that face — a rectangle tube sized to the face, growing
                // out along its normal. Otherwise a default circle at the ground.
                if (k === "l") {
                    const selCell = st.selection ? st.cells[st.selection.cellId] : null;
                    let base:
                        | {placement: number[][]; width: number; height: number}
                        | undefined;
                    if (
                        selCell &&
                        selCell.kind === "cell" &&
                        st.selection?.kind === "face" &&
                        st.selection.faceIndex != null &&
                        BOX_FACE_SIDES[st.selection.faceIndex]
                    ) {
                        const fi = st.selection.faceIndex;
                        const side = BOX_FACE_SIDES[fi];
                        const inPlane = ([0, 1, 2] as const).filter((a) => a !== side.axis) as [
                            0 | 1 | 2,
                            0 | 1 | 2,
                        ];
                        const [a1, a2] = inPlane;
                        const U: Vec3 = [0, 0, 0];
                        U[a1] = 1;
                        const V: Vec3 = [0, 0, 0];
                        V[a2] = 1;
                        const N: Vec3 = [0, 0, 0];
                        N[side.axis] = side.positive ? 1 : -1;
                        const c = faceCenter(selCell, fi); // model space
                        base = {
                            placement: [
                                [U[0], V[0], N[0], c[0]],
                                [U[1], V[1], N[1], c[1]],
                                [U[2], V[2], N[2], c[2]],
                                [0, 0, 0, 1],
                            ],
                            width: selCell.size[a1],
                            height: selCell.size[a2],
                        };
                    }
                    st.addLoftMember(base);
                    const members = useCellBuilderStore.getState().loftMembers;
                    const last = members[members.length - 1];
                    if (last) setLoftActive(last.NAME, 0);
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                // A: enter add-cell placement (pointer ghost).
                if (k === "a") {
                    st.setMode("add-cell");
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                // I: keyboard equipment insert (pick type + host cell, then type
                // the local X,Y). No cell needed to start.
                if (k === "i") {
                    st.setMode("idle");
                    if (startEquipInsert()) {
                        ev.preventDefault();
                        ev.stopPropagation();
                    }
                    return;
                }
                // O: keyboard opening on the selected cell FACE (numeric X,Y,W,H,
                // depth). No-op unless a space-cell face is selected.
                if (k === "o") {
                    if (
                        cell?.kind === "cell" &&
                        st.selection?.kind === "face" &&
                        st.selection.faceIndex != null &&
                        startOpeningOnFace(cell, st.selection.faceIndex)
                    ) {
                        ev.preventDefault();
                        ev.stopPropagation();
                    }
                    return;
                }
                // Tab: cycle selection granularity cell -> face -> edge.
                if (k === "tab" && st.selection) {
                    st.cycleSelectMode(1);
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                // N / P: next / previous cell.
                if (k === "n" || k === "p") {
                    st.selectAdjacentCell(k === "n" ? 1 : -1);
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                // Arrow keys: SPATIAL face navigation. With a box-cell face
                // selected, walk to the edge-adjacent face that lies toward the
                // arrow ON SCREEN (Right = the neighbour whose outward normal
                // projects most to screen-right, etc), using the live camera
                // basis. Consumes the key so the camera doesn't also pan; only
                // active in face mode on a box cell (bare arrows are otherwise
                // free — globals use Shift+arrows for tree traversal).
                if (
                    (ev.key === "ArrowUp" ||
                        ev.key === "ArrowDown" ||
                        ev.key === "ArrowLeft" ||
                        ev.key === "ArrowRight") &&
                    cell?.kind === "cell" &&
                    st.selection?.kind === "face" &&
                    st.selection.faceIndex != null
                ) {
                    const camObj = cameraRef.current;
                    if (camObj) {
                        const camRight = new THREE.Vector3()
                            .setFromMatrixColumn(camObj.matrixWorld, 0)
                            .normalize();
                        const camUp = new THREE.Vector3()
                            .setFromMatrixColumn(camObj.matrixWorld, 1)
                            .normalize();
                        const dir =
                            ev.key === "ArrowUp"
                                ? "up"
                                : ev.key === "ArrowDown"
                                  ? "down"
                                  : ev.key === "ArrowLeft"
                                    ? "left"
                                    : "right";
                        const nb = neighbourFaceInDirection(
                            st.selection.faceIndex,
                            dir,
                            [camRight.x, camRight.y, camRight.z],
                            [camUp.x, camUp.y, camUp.z],
                        );
                        if (nb != null && nb !== st.selection.faceIndex) {
                            st.setSelection({kind: "face", cellId: cell.id, faceIndex: nb});
                        }
                    }
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                // F / D: next / previous element — faces / edges, or loft stations.
                if ((k === "f" || k === "d") && cell) {
                    const dir = k === "f" ? 1 : -1;
                    if (cell.kind === "loft" && cell.loft) {
                        const member = loftMemberByName(cell.loft.member);
                        if (member) {
                            const cur =
                                loftActive && loftActive.member === member.NAME
                                    ? loftActive.index
                                    : cell.loft.bay;
                            const n = member.STATIONS.length;
                            setLoftActive(member.NAME, ((cur + dir) % n + n) % n);
                        }
                    } else {
                        st.cycleSelectionElement(dir);
                    }
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                // T: loft station retype (rectangle<->circle) or cell-type cycle.
                if (k === "t") {
                    if (cell?.kind === "loft" && cell.loft && loftActive) {
                        const member = loftMemberByName(cell.loft.member);
                        const station = member?.STATIONS[loftActive.index];
                        if (member && station) {
                            st.setLoftStationType(
                                member.NAME,
                                loftActive.index,
                                station.TYPE === "circle" ? "rectangle" : "circle",
                            );
                        }
                    } else {
                        st.cycleCellType(1);
                    }
                    ev.preventDefault();
                    ev.stopPropagation();
                    return;
                }
                // E: interactive extrude — box cell from its selected face, or
                // extend a loft stack up its spine. No face selected = no-op (Q1).
                if (k === "e") {
                    let started = false;
                    if (cell?.kind === "loft") {
                        started = startLoftExtend(cell);
                    } else if (
                        cell?.kind === "cell" &&
                        st.selection?.kind === "face" &&
                        st.selection.faceIndex != null
                    ) {
                        started = startCellExtrude(cell, st.selection.faceIndex);
                    }
                    if (started) {
                        ev.preventDefault();
                        ev.stopPropagation();
                    }
                    return;
                }
                // 1-9: pick a cell type directly from the advertised catalog.
                if (/^[1-9]$/.test(ev.key)) {
                    const types = st.cellTypes;
                    const idx = Number(ev.key) - 1;
                    if (idx < types.length) {
                        st.setSelectedCellType(types[idx].slug);
                        ev.preventDefault();
                        ev.stopPropagation();
                    }
                    return;
                }
            }
        }

        // Escape while typing in a field (the HUD's numeric inputs) blurs the
        // field — don't also unwind the selection/gizmo underneath.
        if (ev.key !== "Escape" || inField) return;
        // The insert popover owns its own Escape (it closes itself); don't also
        // unwind the selection underneath it.
        if (st.insertMenu) return;
        // An active axis-locked modal move: Escape cancels it (restore the cell)
        // and drops the lock, without also tearing down the gizmo/selection.
        if (modalMove) {
            endModalMove(true);
            st.setGizmoAxisLock(null);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        // Escape unwinds one layer at a time: menu → gizmo → add-mode → selection.
        if (st.portMenu) {
            st.closePortMenu();
        } else if (st.portGizmo) {
            st.stopPortGizmo();
        } else if (st.contextMenu) {
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
    syncPortGizmo();
    rebuildCompanions();
    // Its own subscription, so a companion change never re-runs the editable
    // rebuild (which clears hover, selection and the mesh index).
    const unsubCompanions = useCompanionModelStore.subscribe((s, prev) => {
        if (s.companions !== prev.companions) rebuildCompanions();
    });

    const unsub = useCellBuilderStore.subscribe((s, prev) => {
        // Leaving an add mode drops any in-progress numeric placement.
        if (
            placeEntry &&
            s.mode !== prev.mode &&
            !(s.mode === "add-cell" || s.mode === "add-opening" || s.mode === "add-equipment")
        ) {
            endPlaceEntry();
        }
        // Drop the keyboard equipment / opening flows if the model closed or the
        // cell they target vanished (e.g. deleted / undone underneath them).
        if (equipEntry && (!s.active || !s.cells[equipEntry.hostId])) endEquipEntry();
        if (openEntry && (!s.active || !s.cells[openEntry.cellId])) endOpenEntry();
        // equipmentCad flips whether equipment boxes fit their CAD or fall back
        // to LX/LY/LZ, so refit on toggle.
        if (s.cells !== prev.cells || s.active !== prev.active || s.equipmentCad !== prev.equipmentCad) rebuild();
        else if (s.selection !== prev.selection || s.selectedCellIds !== prev.selectedCellIds)
            refreshFaceStyles();
        // "Show as CAD" toggles + any cell/type change re-seat the CAD previews
        // (after rebuild() has re-made the boxes so applyCellVisibility hides the
        // right ones).
        if (
            s.cadPreviewCells !== prev.cadPreviewCells ||
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.equipmentTypes !== prev.equipmentTypes
        )
            rebuildCadPreviews();
        // Keep the keyboard "active loft station" in step with the selection:
        // reset it when the selected loft MEMBER changes (preserving the index
        // within the same member, so F/D + setLoftActive don't fight), and clear
        // it when the pick isn't a loft band.
        if (s.selection !== prev.selection) {
            const sc = s.selection ? s.cells[s.selection.cellId] : null;
            if (sc && sc.kind === "loft" && sc.loft) {
                if (!loftActive || loftActive.member !== sc.loft.member)
                    loftActive = {member: sc.loft.member, index: sc.loft.bay};
            } else {
                loftActive = null;
            }
        }
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
            s.gizmoVertexSnap !== prev.gizmoVertexSnap ||
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.gridStep !== prev.gridStep ||
            s.cellsVisible !== prev.cellsVisible
        ) {
            syncGizmo();
        }
        if (
            s.portGizmo !== prev.portGizmo ||
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.portsOverlayVisible !== prev.portsOverlayVisible ||
            s.cellsVisible !== prev.cellsVisible ||
            s.gizmoVertexSnap !== prev.gizmoVertexSnap ||
            s.gridStep !== prev.gridStep
        ) {
            syncPortGizmo();
        }
        if (s.active !== prev.active || s.gridStep !== prev.gridStep) syncBuilderGrid();
        if (s.cellsVisible !== prev.cellsVisible) {
            cellsGroup.visible = s.cellsVisible;
            cadPreviewGroup.visible = s.cellsVisible;
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
        // A model appearing/disappearing (e.g. the compiled CAD GLB overlay)
        // changes which equipment meshes are in the scene; refit CAD-backed
        // equipment boxes to the newly available geometry. Deferred a frame so
        // the freshly added meshes' world matrices are settled before we read
        // their bounds.
        if (s.loadedSourceNames !== prev.loadedSourceNames && useCellBuilderStore.getState().equipmentCad) {
            requestAnimationFrame(() => rebuild());
        }
    });

    return () => {
        unsub();
        unsubCompanions();
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
        portGizmo.detach();
        portGizmo.dispose();
        scene.remove(portGizmoHelper);
        guideLine.geometry.dispose();
        (guideLine.material as THREE.Material).dispose();
        container.remove(guideLine);
        snapTex.dispose();
        (snapMarker.material as THREE.Material).dispose();
        scene.remove(snapMarker);
        readoutTex.dispose();
        (readout.material as THREE.Material).dispose();
        scene.remove(readout);
        ringPreview.geometry.dispose();
        (ringPreview.material as THREE.Material).dispose();
        container.remove(ringPreview);
        faceOverlay.geometry.dispose();
        (faceOverlay.material as THREE.Material).dispose();
        container.remove(faceOverlay);
        disposeResizeHandles();
        hiddenDefaultGrids.forEach((g) => (g.visible = true));
        hiddenDefaultGrids.length = 0;
        disposeBuilderGrid();
        clearPorts();
        // Free the CAD-preview cache prototypes (the scene clones share their
        // resources, so this is the single owner that disposes them).
        for (const proto of cadPreviewCache.values()) {
            if (proto === "loading" || proto === "error") continue;
            proto.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(m);
            });
        }
        cadPreviewCache.clear();
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
