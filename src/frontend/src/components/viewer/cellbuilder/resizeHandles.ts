/**
 * Face-handle RESIZE gizmo and gizmo reconciliation.
 *
 * Owns: the six touch-friendly spheres at a cell's face centres (each dragged
 * with the same face-offset math as a face drag) and `syncGizmo`, which
 * reconciles every gizmo with the current selection and mode.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {BOX_FACE_SIDES, faceCenter} from "@/utils/cellbuilder/snap";
import {HANDLE_AXIS_COLOR} from "./sceneConstants";
import type {CellBuilderScene} from "./sceneContext";
import {reconcileModalMove} from "./cellGizmo";


export const disposeResizeHandles = (ctx: CellBuilderScene) => {
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
    const sel = st.selection;
    const cell = sel ? st.cells[sel.cellId] : null;
    // Equipment is sized by its type, not free-resized in the scene — no
    // resize handles for it (Move still works).
    const show = !!(
        st.active && st.gizmoMode === "resize" && cell && cell.kind === "cell" && st.cellsVisible
    );
    ctx.resizeGroup.visible = show;
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
        ctx.resizeGroup.add(mesh);
    }
};

// Reconcile the gizmos with the current selection/mode. Skipped repositioning
// of the translate proxy mid-drag so it never fights the pointer.
export const syncGizmo = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const runtimeCam = getViewerRuntime().camera.current;
    if (runtimeCam) ctx.gizmo.camera = runtimeCam;
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
        ctx.gizmo.setTranslationSnap(
            st.gizmoVertexSnap ? null : st.gridStep > 0 ? st.gridStep : null,
        );
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
    } else if (rotateOn && cell) {
        // Rings snap to 15°; the manual panel supplies exact angles. The
        // proxy sits at the footprint centre (the compiler's pivot) and is
        // seeded from the cell's current rotation so the gizmo starts aligned.
        ctx.gizmo.setRotationSnap(THREE.MathUtils.degToRad(15));
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
    requestRender();
};

// Begin a face drag (positive face scales size; negative face shifts origin).
// ``immediate`` starts it right away (resize-handle grab) vs. after
// DRAG_START_PX of travel (a face press, once face-drag resizing is on).
