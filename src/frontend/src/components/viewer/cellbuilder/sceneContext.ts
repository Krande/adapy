/**
 * The builder SCENE CONTEXT.
 *
 * Owns: every three.js object the builder puts in the scene (the offset
 * container and its subgroups, the placement ghost, hover/selection overlays,
 * both transform gizmos, the snap marker and the on-canvas readout) plus the
 * in-flight interaction state the modules share. One object, created once per
 * viewer, passed explicitly to every module — nothing here is a closure over
 * React scope.
 *
 * STRUCTURE FRAMES. A structure's items are stored relative to its origin
 * (utils/cellbuilder/groups `CellGroup.origin`). The drawn items go in a group per
 * structure under each of their subgroups, moved to its origin (./structureFrames.ts);
 * the editing helpers -- the gizmo proxy, the resize handles, the dimension lines,
 * the selection overlays -- sit in `editFrame`, moved to the SELECTED item's
 * structure, and the placement ghost and the builder grid in `placeFrame` (the
 * active structure's while an add mode places a new box). So the editing code
 * works in an item's own, stored coordinates; `offsetVec` / `worldToModel` map
 * between the world and a frame.
 */

import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import type {CellBox, Vec3} from "@/utils/cellbuilder/snap";
import {GHOST_COLOR, HOVER_EDGE_COLOR, HOVER_EDGE_WIDTH, SELECTED_EDGE_COLOR, SELECTED_EDGE_WIDTH, SELECTED_FACE_COLOR} from "./sceneConstants";
import type {DragState, EquipEntry, HoveredEdge, HoveredFace, LoftActive, ModalMove, NumEntry, OpenEntry, PendingGizmoExit, PendingSelect, PlaceEntry} from "./sceneTypes";
import type {BuilderViewHost} from "./viewHost";

export function createCellBuilderScene(host: BuilderViewHost) {
    const scene = host.scene;
    const container = new THREE.Group();
    container.name = "__cellbuilder__";
    container.userData.__excludeFromFit = true;
    scene.add(container);
    // Cell meshes live in their own subgroup so "hide cells" toggles them
    // without touching the ghost or the builder grid.
    const cellsGroup = new THREE.Group();
    container.add(cellsGroup);
    // The selected item's structure frame and the placement frame (see the head).
    const editFrame = new THREE.Group();
    editFrame.name = "__cellbuilder_edit_frame__";
    container.add(editFrame);
    const placeFrame = new THREE.Group();
    placeFrame.name = "__cellbuilder_place_frame__";
    container.add(placeFrame);
    // The edited port's equipment's structure frame (its gizmo proxy).
    const portFrame = new THREE.Group();
    portFrame.name = "__cellbuilder_port_frame__";
    container.add(portFrame);

    // The plugins' refs (refsOverlay.ts): beside the cells, not in `cellsGroup`, so a cell
    // rebuild leaves them be and no cell-only walk (snapping, the selection rectangle) meets one.
    const refsGroup = new THREE.Group();
    refsGroup.name = "__cellbuilder_refs__";
    container.add(refsGroup);
    const refMeshById = new Map<string, THREE.Mesh>();
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
    // COG marker overlay (cogOverlay.ts): drawn for the selected equipment only -- no
    // view toggle, so its own subgroup only ever needs rebuilding, never a visibility flip of its
    // own. Inherits the container's model offset, same as the ports overlay.
    const cogGroup = new THREE.Group();
    container.add(cogGroup);
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
    placeFrame.add(ghost); // inherits the model offset and the placement frame's
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
        editFrame.add(line);
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
    editFrame.add(faceOverlay);
    const gizmoProxy = new THREE.Object3D();
    gizmoProxy.userData.__excludeFromFit = true;
    // Read/seed the proxy's orientation in the same ZYX order the store + the
    // compiler compose rotations (Rz·Ry·Rx), so a per-axis rotation the gizmo
    // produces round-trips to identical ROT_X/Y/Z on the built equipment.
    gizmoProxy.rotation.order = "ZYX";
    editFrame.add(gizmoProxy);
    const gizmo = new TransformControls(host.camera(), host.input());
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
    editFrame.add(guideLine);
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
    portFrame.add(portProxy);
    const portGizmo = new TransformControls(host.camera(), host.input());
    portGizmo.setSpace("world");
    const portGizmoHelper = portGizmo.getHelper();
    portGizmoHelper.userData.__excludeFromFit = true;
    portGizmoHelper.visible = false;
    scene.add(portGizmoHelper);
    const portRaycaster = new THREE.Raycaster();
    portRaycaster.layers.set(1);
    const resizeGroup = new THREE.Group();
    resizeGroup.visible = false;
    editFrame.add(resizeGroup);
    // The resized cell's dimension lines (cellDimensions.ts): their own group, so the
    // resize handles' raycast never picks a line or a label.
    const dimensionGroup = new THREE.Group();
    dimensionGroup.visible = false;
    dimensionGroup.userData.__excludeFromFit = true;
    editFrame.add(dimensionGroup);
    // How far the moved cell has travelled (G): the start outline and one offset line per
    // axis (cellDimensions.ts), apart from the resize dimensions.
    const moveMeasureGroup = new THREE.Group();
    moveMeasureGroup.visible = false;
    moveMeasureGroup.userData.__excludeFromFit = true;
    editFrame.add(moveMeasureGroup);
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
    // station's NEW dimensions so the ring visibly scales as you type. In the
    // edited loft band's frame (which carries the model offset).
    const ringPreview = new THREE.LineLoop(
        new THREE.BufferGeometry(),
        new THREE.LineBasicMaterial({color: GHOST_COLOR, depthTest: false, transparent: true, opacity: 0.95}),
    );
    ringPreview.userData.__excludeFromFit = true;
    ringPreview.renderOrder = 6;
    ringPreview.visible = false;
    editFrame.add(ringPreview);

    return {
        /** The viewport the builder is drawn in (./viewHost.ts). */
        host,
        scene,
        container,
        cellsGroup,
        editFrame,
        placeFrame,
        portFrame,
        refsGroup,
        refMeshById,
        companionsGroup,
        portsGroup,
        cogGroup,
        cadPreviewGroup,
        cadPreviewCache,
        cadPreviewShown,
        portMarkerGeom,
        ghost,
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
        dimensionGroup,
        moveMeasureGroup,
        readoutCanvas,
        readoutTex,
        readout,
        ringPreview,

        /** The info label's sprite (infoLabel.ts), made the first time it shows. */
        infoLabel: null as THREE.Sprite | null,
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
        /** A picked structure the move gizmo is dragging (./structureFrames.ts): its origin and
         * the gizmo's centre when the drag began, and the origin it is drawn at now. Written to
         * the store once, on release. Null between drags. */
        structureDrag: null as {name: string; start: Vec3; startCentre: Vec3; origin: Vec3} | null,
        /** A multi-item move in progress (G on 2+ selected items) -- each entry's
         * (cell/ref/rider equipment) own start origin, the loft members' own running delta (they
         * move incrementally, moveLoftMember takes a delta not an absolute origin) and the
         * group's gizmo centre when the drag began. Null between drags. */
        multiMove: null as {
            startCentre: Vec3;
            entries: {kind: "cell" | "ref"; id: string; start: Vec3}[];
            loftApplied: Map<string, Vec3>;
        } | null,
        /** The cell being moved (gizmo drag or modal move) and its box when the move
         * began, measured live by the move dimension lines. Null when nothing moves. */
        moveMeasure: null as {cellId: string; ref?: boolean; start: CellBox} | null,
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
        /** Where the right button went down: a right-click that then moved was a drag (the camera's), not a menu. */
        rightPress: null as {x: number; y: number} | null,
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

/** A structure frame's place: a frame group's position, or a structure origin. */
export type FrameAt = THREE.Vector3 | readonly number[];

const atXYZ = (at: FrameAt): readonly [number, number, number] =>
    at instanceof THREE.Vector3 ? [at.x, at.y, at.z] : [at[0] ?? 0, at[1] ?? 0, at[2] ?? 0];

/** Where a frame's [0, 0, 0] is in the world: the viewer's model translation the
 * container carries plus the frame's structure origin -- by default the edit
 * frame's (the selected item's structure). */
export const offsetVec = (ctx: CellBuilderScene, at: FrameAt = ctx.editFrame.position): THREE.Vector3 => {
    const [x, y, z] = atXYZ(at);
    return new THREE.Vector3(ctx.container.position.x + x, ctx.container.position.y + y, ctx.container.position.z + z);
};

/** A world-space point in a frame's (by default the edit frame's) coordinates. */
export const worldToModel = (ctx: CellBuilderScene, p: THREE.Vector3, at: FrameAt = ctx.editFrame.position): Vec3 => {
    const off = offsetVec(ctx, at);
    return [p.x - off.x, p.y - off.y, p.z - off.z];
};
