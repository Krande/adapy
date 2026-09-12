/**
 * The builder SCENE CONTEXT.
 *
 * Owns: every three.js object the builder puts in the scene (the offset
 * container and its subgroups, the placement ghost, hover/selection overlays,
 * both transform gizmos, the snap marker and the on-canvas readout) plus the
 * in-flight interaction state the modules share. One object, created once per
 * viewer, passed explicitly to every module — nothing here is a closure over
 * React scope.
 */

import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import type {CellBox, Vec3} from "@/utils/cellbuilder/snap";
import {GHOST_COLOR, HOVER_EDGE_COLOR, HOVER_EDGE_WIDTH, SELECTED_EDGE_COLOR, SELECTED_EDGE_WIDTH, SELECTED_FACE_COLOR} from "./sceneConstants";
import type {DragState, EquipEntry, HoveredEdge, HoveredFace, LoftActive, ModalMove, NumEntry, OpenEntry, PendingGizmoExit, PendingSelect, PlaceEntry} from "./sceneTypes";

export function createCellBuilderScene(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
) {
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
    const ghost = new THREE.Mesh(
        new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshBasicMaterial({color: GHOST_COLOR, transparent: true, opacity: 0.35, depthWrite: false}),
    );
    ghost.visible = false;
    container.add(ghost); // inherits the model offset
    // While a procedural model is open, the scene's static 1 m helper grid is
    // swapped for a builder grid whose line spacing IS the snap gridStep (and
    // which lives inside the container, so its intersections are exactly the
    // model-space points quantize() snaps to).
    const hiddenDefaultGrids: THREE.GridHelper[] = [];
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
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
    const gizmoProxy = new THREE.Object3D();
    gizmoProxy.userData.__excludeFromFit = true;
    // Read/seed the proxy's orientation in the same ZYX order the store + the
    // compiler compose rotations (Rz·Ry·Rx), so a per-axis rotation the gizmo
    // produces round-trips to identical ROT_X/Y/Z on the built equipment.
    gizmoProxy.rotation.order = "ZYX";
    container.add(gizmoProxy);
    const gizmo = new TransformControls(getViewerRuntime().camera.current ?? (camera as THREE.Camera), renderer.domElement);
    gizmo.setSpace("world");
    const gizmoHelper = gizmo.getHelper();
    gizmoHelper.userData.__excludeFromFit = true;
    gizmoHelper.visible = false;
    scene.add(gizmoHelper);
    const guideLine = new THREE.Line(
        new THREE.BufferGeometry(),
        new THREE.LineBasicMaterial({depthTest: false, transparent: true, opacity: 0.6}),
    );
    guideLine.userData.__excludeFromFit = true;
    guideLine.visible = false;
    guideLine.renderOrder = 2;
    container.add(guideLine);
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
    const portProxy = new THREE.Object3D();
    portProxy.userData.__excludeFromFit = true;
    container.add(portProxy);
    const portGizmo = new TransformControls(getViewerRuntime().camera.current ?? (camera as THREE.Camera), renderer.domElement);
    portGizmo.setSpace("world");
    const portGizmoHelper = portGizmo.getHelper();
    portGizmoHelper.userData.__excludeFromFit = true;
    portGizmoHelper.visible = false;
    scene.add(portGizmoHelper);
    const portRaycaster = new THREE.Raycaster();
    portRaycaster.layers.set(1);
    const resizeGroup = new THREE.Group();
    resizeGroup.visible = false;
    container.add(resizeGroup);
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

    return {
        renderer,
        scene,
        camera,
        container,
        cellsGroup,
        companionsGroup,
        portsGroup,
        cadPreviewGroup,
        cadPreviewCache,
        cadPreviewShown,
        portMarkerGeom,
        ghost,
        hiddenDefaultGrids,
        raycaster,
        pointer,
        meshById,
        hoverEdgeLine,
        selectedEdgeLine,
        faceOverlay,
        gizmoProxy,
        gizmo,
        gizmoHelper,
        guideLine,
        snapTex,
        snapMarker,
        portProxy,
        portGizmo,
        portGizmoHelper,
        portRaycaster,
        resizeGroup,
        readoutCanvas,
        readoutTex,
        readout,
        ringPreview,

        /** The ghost's current box while an add mode previews a placement. */
        ghostBox: null as CellBox | null,
        /** The step-spaced grid swapped in for the scene's default one. */
        builderGrid: null as THREE.GridHelper | null,
        builderGridStep: -1,
        /** The face drag in progress, if any. */
        drag: null as DragState | null,
        hovered: null as HoveredFace | null,
        hoveredEdge: null as HoveredEdge | null,
        /** Baseline for the loft member-move gizmo: the last APPLIED proxy
         * position (model frame). moveLoftMember advances it by the quantized
         * delta each frame, so residual sub-grid pointer travel carries over and
         * the member steps in exact grid multiples. Null between drags. */
        loftDragLast: null as THREE.Vector3 | null,
        modalMove: null as ModalMove | null,
        /** Equipment captured to ride along with the space cell being translated. */
        translateEquip: [] as string[],
        /** Outward world direction of the edited port at rotate-drag start; the
         * rotate delta accumulates from identity onto it. */
        portRotateStartDir: null as THREE.Vector3 | null,
        /** A tap on empty space while a gizmo is active exits it. Recorded on
         * pointerdown, resolved on pointerup (a drag past DRAG_START_PX = orbit). */
        pendingGizmoExit: null as PendingGizmoExit | null,
        /** A tap on a cell that selects on pointerup — used when face-drag resizing
         * is OFF, where we must NOT grab the pointer on pointerdown (so a drag over
         * a cell still orbits). Any travel past DRAG_START_PX cancels it. */
        pendingSelect: null as PendingSelect | null,
        longPressTimer: null as ReturnType<typeof setTimeout> | null,
        longPressStartX: 0,
        longPressStartY: 0,
        numEntry: null as NumEntry | null,
        /** Active loft station for keyboard station edits (S/T) — decoupled from
         * the bay selection so E's freshly-added top station and L's base station
         * are both directly addressable. */
        loftActive: null as LoftActive | null,
        placeEntry: null as PlaceEntry | null,
        equipEntry: null as EquipEntry | null,
        openEntry: null as OpenEntry | null,
    };
}

/** Everything the builder's imperative modules share. Its type is inferred from
 * the factory above, so adding a field needs no second declaration. */
export type CellBuilderScene = ReturnType<typeof createCellBuilderScene>;

    // Loaded GLBs are shifted by modelState.translation (bbox centering +
    // z-lift). The builder authors model-space coordinates, so the container
    // applies the same shift — cells and the compiled structure stay aligned.
export const syncOffset = (ctx: CellBuilderScene) => {
    const t = useModelState.getState().translation;
    ctx.container.position.set(t?.x ?? 0, t?.y ?? 0, t?.z ?? 0);
    requestRender();
};

/** The viewer's model translation the builder container carries. */
export const offsetVec = (ctx: CellBuilderScene): THREE.Vector3 => ctx.container.position;

/** A world-space point in the builder's model frame. */
export const worldToModel = (ctx: CellBuilderScene, p: THREE.Vector3): Vec3 => [
    p.x - ctx.container.position.x,
    p.y - ctx.container.position.y,
    p.z - ctx.container.position.z,
];
