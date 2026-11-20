/**
 * Face-handle RESIZE gizmo and gizmo reconciliation.
 *
 * Owns: the six touch-friendly spheres at a cell's face centres (each dragged
 * with the same face-offset math as a face drag) and `syncGizmo`, which
 * reconciles every gizmo with the current selection and mode.
 */

import * as THREE from "three";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {boxFrameMatrix, equipmentCadFitted, resizeRefusal} from "@/utils/cellbuilder/cellResize";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {BOX_FACE_SIDES, faceCenter} from "@/utils/cellbuilder/snap";
import {GIZMO_ROTATE_SNAP_DEG} from "@/utils/gizmo/transformKeys";
import {freeMove} from "@/utils/gizmo/freeMove";
import {screenSizedScale} from "@/utils/gizmo/screenSize";
import {HANDLE_AXIS_COLOR} from "./sceneConstants";
import type {CellBuilderScene} from "./sceneContext";
import {reconcileModalMove, reconcileMoveMeasure} from "./cellGizmo";
import {syncCellDimensions} from "./cellDimensions";
import {boxMoves, selectedBox} from "./gizmoBox";


/**
 * Whether the resize gizmo takes `cell` (see `resizeRefusal`). The one reading the menus, the
 * commands and the scene share (`equipmentCadFitted`, store-only): the scene's own fit of a box
 * to a loaded mesh of the cell's name is not read here, since a compiled result names every
 * equipment's body after its cell, and that made the scene refuse what the menus offered.
 */
export const resizable = (_ctx: CellBuilderScene, cell: BuilderCell): boolean =>
    resizeRefusal(cell.kind, {cadFitted: equipmentCadFitted(cell, useCellBuilderStore.getState())}) === null;

/**
 * The step the move gizmo itself snaps its arrows to: the grid step; none with vertex magnetism on
 * (the snap in objectChange owns where the box lands) or while the left Shift key is held (free move).
 */
export const translationSnapFor = (st: {gizmoVertexSnap: boolean; gridStep: number}): number | null =>
    freeMove() || st.gizmoVertexSnap ? null : st.gridStep > 0 ? st.gridStep : null;

/** A resize handle's radius, as a share of the view's height: the same on screen at any zoom. */
export const HANDLE_SCREEN_SHARE = 0.011;

const handleAt = new THREE.Vector3();

/**
 * Keep the resize handles one size on screen, in perspective and drawn orthographically: each time the
 * renderer brings their group up to date, each handle is scaled from the view's height where it is.
 * Returns the undo.
 */
export const keepHandlesScreenSized = (ctx: CellBuilderScene): (() => void) => {
    const group = ctx.resizeGroup;
    const own = group.updateMatrixWorld;
    group.updateMatrixWorld = function (this: THREE.Group, force?: boolean) {
        own.call(this, force);
        const camera = getViewerRuntime().camera.current ?? ctx.camera;
        if (!this.visible || !camera) return;
        for (const handle of this.children) {
            handle.getWorldPosition(handleAt);
            handle.scale.setScalar(screenSizedScale(camera, handleAt, HANDLE_SCREEN_SHARE));
            handle.updateMatrixWorld(true);
        }
    };
    return () => {
        group.updateMatrixWorld = own;
    };
};

export const disposeResizeHandles =(ctx: CellBuilderScene) => {
    for (let i = ctx.resizeGroup.children.length - 1; i >= 0; i--) {
        const o = ctx.resizeGroup.children[i] as THREE.Mesh;
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
        ctx.resizeGroup.remove(o);
    }
};

export const rebuildResizeHandles = (ctx: CellBuilderScene) => {
    disposeResizeHandles(ctx);
    const st = useCellBuilderStore.getState();
    const box = selectedBox(st);
    const cell = box;
    // Spaces, openings and equipment resize; a loft band, or an equipment box fitted
    // to its CAD model, does not (cellResize.ts says why). A ref resizes when its plugin allows it.
    const takes = box ? (box.ref ? box.ref.edit.resize : !!box.cell && resizable(ctx, box.cell)) : false;
    const show = !!(st.active && st.gizmoMode === "resize" && cell && st.cellsVisible && takes);
    ctx.resizeGroup.visible = show;
    // A rotated equipment's handles and dimensions sit in its own (rotated) frame.
    const rotation = box?.cell?.kind === "equipment" ? box.cell.rotation : null;
    const frame = cell ? boxFrameMatrix(cell, rotation) : new THREE.Matrix4();
    ctx.resizeGroup.matrixAutoUpdate = false;
    ctx.resizeGroup.matrix.copy(frame);
    const r = cell ? Math.max(0.15, 0.1 * Math.min(cell.size[0], cell.size[1], cell.size[2])) : 0;
    // Its three dimensions, just outside the box (a handle's radius off it).
    syncCellDimensions(ctx, show && cell ? cell : null, r, frame);
    if (!show || !cell) return;
    for (let fi = 0; fi < BOX_FACE_SIDES.length; fi++) {
        const side = BOX_FACE_SIDES[fi];
        const c = faceCenter(cell, fi);
        const mesh = new THREE.Mesh(
            // A unit sphere: keepHandlesScreenSized scales it to its size on screen.
            new THREE.SphereGeometry(1, 16, 12),
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
        if (box?.ref) mesh.userData.__refId = cell.id;
        else mesh.userData.__cellId = cell.id;
        ctx.resizeGroup.add(mesh);
    }
    // With the handles in: a pointer that raycasts them before the next render finds them in the frame.
    ctx.resizeGroup.updateMatrixWorld(true);
};

// Reconcile the gizmos with the current selection/mode. Skipped repositioning
// of the translate proxy mid-drag so it never fights the pointer.
export const syncGizmo = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const runtimeCam = getViewerRuntime().camera.current;
    if (runtimeCam) ctx.gizmo.camera = runtimeCam;
    const box = selectedBox(st);
    const cell = box;
    // Translate works for every kind — including a loft band, whose gizmo
    // moves the whole member (see the loft branch in objectChange). The
    // proxy still seeds from the band's bounding-box centre below. A ref
    // moves when its plugin allows it.
    const translateOn = !!(st.active && st.gizmoMode === "translate" && box && boxMoves(box) && st.cellsVisible);
    // Rotate is equipment-only — spaces stay axis-aligned lattice boxes.
    const equipment = box?.cell?.kind === "equipment" ? box.cell : null;
    const rotateOn = !!(st.active && st.gizmoMode === "rotate" && equipment && st.cellsVisible);
    if (translateOn && cell) {
        // With vertex magnetism on, let the widget move continuously so the
        // snap in objectChange (corner-to-corner, or axis-locked) fully owns
        // where the cell lands. Otherwise fall back to the grid step so the
        // gizmo itself steps on the grid. Free move (left Shift held): no step.
        ctx.gizmo.setTranslationSnap(translationSnapFor(st));
        if (!ctx.gizmo.dragging) {
            ctx.gizmoProxy.rotation.set(0, 0, 0);
            ctx.gizmoProxy.position.set(
                cell.origin[0] + cell.size[0] / 2,
                cell.origin[1] + cell.size[1] / 2,
                cell.origin[2] + cell.size[2] / 2,
            );
        }
        if (ctx.gizmo.object !== ctx.gizmoProxy) ctx.gizmo.attach(ctx.gizmoProxy);
        ctx.gizmo.setMode("translate");
        ctx.gizmoHelper.visible = true;
    } else if (rotateOn && equipment) {
        const cell = equipment;
        // Rings snap to 15°; the manual panel supplies exact angles. The
        // proxy sits at the footprint centre (the compiler's pivot) and is
        // seeded from the cell's current rotation so the gizmo starts aligned.
        ctx.gizmo.setRotationSnap(THREE.MathUtils.degToRad(GIZMO_ROTATE_SNAP_DEG));
        if (!ctx.gizmo.dragging) {
            ctx.gizmoProxy.position.set(
                cell.origin[0] + cell.size[0] / 2,
                cell.origin[1] + cell.size[1] / 2,
                cell.origin[2],
            );
            const rot = cell.rotation ?? [0, 0, 0];
            ctx.gizmoProxy.rotation.set(
                THREE.MathUtils.degToRad(rot[0]),
                THREE.MathUtils.degToRad(rot[1]),
                THREE.MathUtils.degToRad(rot[2]),
            );
        }
        if (ctx.gizmo.object !== ctx.gizmoProxy) ctx.gizmo.attach(ctx.gizmoProxy);
        ctx.gizmo.setMode("rotate");
        ctx.gizmoHelper.visible = true;
    } else {
        if (ctx.gizmo.object) ctx.gizmo.detach();
        ctx.gizmoHelper.visible = false;
    }
    // Axis lock (X/Y/Z keys / HUD) restricts the visible + usable gizmo
    // handle to one axis; null shows all three. Reset to all when detached so
    // a later attach isn't stuck on a stale constraint.
    if (translateOn || rotateOn) {
        const lock = st.gizmoAxisLock;
        ctx.gizmo.showX = lock === null || lock === 0;
        ctx.gizmo.showY = lock === null || lock === 1;
        ctx.gizmo.showZ = lock === null || lock === 2;
    } else {
        ctx.gizmo.showX = ctx.gizmo.showY = ctx.gizmo.showZ = true;
    }
    rebuildResizeHandles(ctx);
    // After the widget visibility is set, let the axis-locked modal move take
    // over (it re-hides the helper it doesn't need).
    reconcileModalMove(ctx);
    // How far the moved cell has come, following the (snapped) move live.
    reconcileMoveMeasure(ctx);
    requestRender();
};

// Begin a face drag (positive face scales size; negative face shifts origin).
// ``immediate`` starts it right away (resize-handle grab) vs. after
// DRAG_START_PX of travel (a face press, once face-drag resizing is on).
