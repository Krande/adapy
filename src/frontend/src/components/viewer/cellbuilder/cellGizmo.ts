/**
 * Cell TRANSLATE/ROTATE gizmo and the axis-locked modal move.
 *
 * Owns: the TransformControls proxy that drives the selected cell, the
 * Blender-style modal move (lock an axis, the cell tracks the pointer, click
 * confirms / Escape cancels), pointer-driven vertex magnetism with its
 * on-screen marker, and carrying a space cell's contained equipment along.
 */

import * as THREE from "three";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {boxCorners, originFromCenter, quantize, type Vec3} from "@/utils/cellbuilder/snap";
import {HANDLE_AXIS_COLOR, SNAP_PX} from "./sceneConstants";
import {offsetVec, type CellBuilderScene} from "./sceneContext";


// --- Blender-style axis-locked modal move ---------------------------------
// When the translate gizmo is locked to an axis (X/Y/Z), the cell tracks the
// pointer along that axis with NO click-drag — move the mouse and it follows;
// left-click confirms, Escape cancels. This makes vertex snapping obvious
// (it's evaluated every pointer move) and matches Blender's G-then-X grab.
// `startT` is seeded on the first move so the cell doesn't jump on activation.
export const showGuideLine = (ctx: CellBuilderScene, axis: 0 | 1 | 2, centerModel: Vec3) => {
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
    ctx.guideLine.geometry.setAttribute("position", new THREE.BufferAttribute(pts, 3));
    ctx.guideLine.geometry.computeBoundingSphere();
    (ctx.guideLine.material as THREE.LineBasicMaterial).color.setHex(HANDLE_AXIS_COLOR[axis]);
    ctx.guideLine.visible = true;
};

export const cellCenterModel = (ctx: CellBuilderScene, cell: BuilderCell): Vec3 => [
    cell.origin[0] + cell.size[0] / 2,
    cell.origin[1] + cell.size[1] / 2,
    cell.origin[2] + cell.size[2] / 2,
];

// Equipment cells whose CENTRE sits inside `cell`'s box (all 3 axes, so the
// two floors of a stacked model don't grab each other's units) — these ride
// along when the space cell is translated. Captured once at drag start.
export const equipContainedIn = (ctx: CellBuilderScene, cell: BuilderCell): string[] => {
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
export const applyCellTranslate = (ctx: CellBuilderScene, cell: BuilderCell, origin: Vec3) => {
    const st = useCellBuilderStore.getState();
    if (cell.kind === "cell" && ctx.translateEquip.length)
        st.moveCellAndEquipment(cell.id, origin, ctx.translateEquip);
    else st.updateCell(cell.id, { origin });
};

// --- Vertex-snap indicator ------------------------------------------------
// A hollow amber square drawn at the neighbour vertex the dragged cell just
// snapped onto, so vertex magnetism is visible while moving. It's a Sprite
// (always faces the camera) with constant on-screen size (sizeAttenuation
// off), depth-test off so it shows through geometry. Lives in world space
// (not the container) — position is the snapped corner + the model offset.
// Show the marker at a snapped neighbour vertex (model-space), or hide it.
export const showSnapMarker = (ctx: CellBuilderScene, targetModel: Vec3 | null) => {
    if (!targetModel) {
        if (ctx.snapMarker.visible) {
            ctx.snapMarker.visible = false;
            requestRender();
        }
        return;
    }
    const off = offsetVec(ctx);
    ctx.snapMarker.position.set(targetModel[0] + off.x, targetModel[1] + off.y, targetModel[2] + off.z);
    ctx.snapMarker.visible = true;
    requestRender();
};


// The neighbour-cell corner (model space) nearest the pointer on screen,
// within SNAP_PX, or null. This is the snap TARGET — the vertex under the
// cursor — so the marker lands exactly where the user is pointing.
export const nearestCornerToPointer = (ctx: CellBuilderScene, excludeCellId: string): Vec3 | null => {
    const cam = getViewerRuntime().camera.current ?? (ctx.camera as THREE.PerspectiveCamera);
    const off = offsetVec(ctx);
    const rect = ctx.renderer.domElement.getBoundingClientRect();
    const px = (ctx.pointer.x * 0.5 + 0.5) * rect.width;
    const py = (-ctx.pointer.y * 0.5 + 0.5) * rect.height;
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
export const computeMove = (
    ctx: CellBuilderScene,
    cell: {id: string; origin: Vec3; size: Vec3},
    center: Vec3,
    axis: 0 | 1 | 2 | null,
): {origin: Vec3; target: Vec3 | null} => {
    const st = useCellBuilderStore.getState();
    const step = st.gridStep > 0 ? st.gridStep : 0.1;
    if (st.gizmoVertexSnap && axis !== null) {
        const target = nearestCornerToPointer(ctx, cell.id);
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

export const startModalMove = (ctx: CellBuilderScene, cell: BuilderCell, axis: 0 | 1 | 2) => {
    // Switching axis/cell mid-grab: close the previous leg's undo step first
    // (keeping its position — a plain re-lock is a confirm, not a cancel).
    if (ctx.modalMove && ctx.modalMove.startT !== null) useCellBuilderStore.getState().endTransaction();
    const centerModel = cellCenterModel(ctx, cell);
    ctx.modalMove = {
        cellId: cell.id,
        axis,
        lineDir: new THREE.Vector3(axis === 0 ? 1 : 0, axis === 1 ? 1 : 0, axis === 2 ? 1 : 0),
        lineOrigin: new THREE.Vector3(centerModel[0], centerModel[1], centerModel[2]).add(offsetVec(ctx)),
        startT: null,
        startBox: {origin: [...cell.origin], size: [...cell.size]},
    };
    showGuideLine(ctx, axis, centerModel);
    ctx.renderer.domElement.style.cursor = "move";
};

export const endModalMove = (ctx: CellBuilderScene, cancel: boolean) => {
    if (!ctx.modalMove) return;
    const mm = ctx.modalMove;
    ctx.modalMove = null;
    if (mm.startT !== null) {
        if (cancel) {
            const st = useCellBuilderStore.getState();
            // Revert the cell — and any equipment that rode along — together.
            if (ctx.translateEquip.length)
                st.moveCellAndEquipment(mm.cellId, [...mm.startBox.origin], ctx.translateEquip);
            else
                st.updateCell(mm.cellId, {origin: [...mm.startBox.origin], size: [...mm.startBox.size]});
        }
        useCellBuilderStore.getState().endTransaction();
    }
    ctx.translateEquip = [];
    ctx.gizmo.enabled = true;
    ctx.guideLine.visible = false;
    ctx.snapMarker.visible = false;
    ctx.renderer.domElement.style.cursor = "";
    requestRender();
};

// Reconcile the modal-move with the store: active whenever the translate
// gizmo is locked to an axis on a (non-loft) cell. Called at the end of
// syncGizmo, so it can re-hide the gizmo helper that syncGizmo just showed.
export const reconcileModalMove = (ctx: CellBuilderScene) => {
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
        if (!ctx.modalMove || ctx.modalMove.cellId !== cell.id || ctx.modalMove.axis !== axis) {
            startModalMove(ctx, cell, axis);
        }
        // Take over from the TransformControls widget: disable it and hide its
        // helper so a confirm-click can't grab a handle, and the pointer drives
        // the move instead.
        ctx.gizmo.enabled = false;
        ctx.gizmoHelper.visible = false;
    } else if (ctx.modalMove) {
        endModalMove(ctx, false);
    }
};
/** Wire the cell gizmo's drag events (camera hand-off + live apply). */
export function installCellGizmo(ctx: CellBuilderScene): void {

    ctx.gizmo.addEventListener("dragging-changed", (e: any) => {
        const st = useCellBuilderStore.getState();
        const runtimeCtl = getViewerRuntime().controls.current;
        if (runtimeCtl) runtimeCtl.enabled = !e.value;
        // Coalesce the whole widget drag into one undo step.
        if (e.value) {
            st.beginTransaction();
            // Seed the loft member-move baseline at the proxy's current (box-
            // centre) position; cleared when the drag ends.
            ctx.loftDragLast = ctx.gizmoProxy.position.clone();
            // Capture the equipment sitting in the cell so they ride along.
            const sel = st.selection;
            const cell = sel ? st.cells[sel.cellId] : null;
            ctx.translateEquip = cell && st.gizmoMode === "translate" ? equipContainedIn(ctx, cell) : [];
        } else {
            st.endTransaction();
            ctx.loftDragLast = null;
            ctx.translateEquip = [];
            showSnapMarker(ctx, null); // drag ended — clear the snap indicator
        }
        requestRender();
    });
    ctx.gizmo.addEventListener("objectChange", () => {
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
                if (!ctx.loftDragLast) ctx.loftDragLast = ctx.gizmoProxy.position.clone();
                const dx = Math.round((ctx.gizmoProxy.position.x - ctx.loftDragLast.x) / step) * step;
                const dy = Math.round((ctx.gizmoProxy.position.y - ctx.loftDragLast.y) / step) * step;
                const dz = Math.round((ctx.gizmoProxy.position.z - ctx.loftDragLast.z) / step) * step;
                if (dx || dy || dz) {
                    st.moveLoftMember(cell.loft.member, [dx, dy, dz]);
                    ctx.loftDragLast.set(ctx.loftDragLast.x + dx, ctx.loftDragLast.y + dy, ctx.loftDragLast.z + dz);
                }
                return;
            }
            // Constrain the snap to the axis actually being dragged. A single-
            // axis handle (the X/Y/Z arrow) only moves the cell along that axis,
            // so a full-3D snap would essentially never fire (a corner can't come
            // within range in the other two axes). Match Blender: snap along the
            // drag axis. An explicit X/Y/Z lock wins; a plane/centre handle (no
            // single axis) falls back to full-3D snap.
            const handle = (ctx.gizmo as unknown as {axis: string | null}).axis;
            const handleAxis =
                handle === "X" ? 0 : handle === "Y" ? 1 : handle === "Z" ? 2 : null;
            const snapAxis = st.gizmoAxisLock ?? handleAxis;
            const {origin, target} = computeMove(ctx,
                cell,
                [ctx.gizmoProxy.position.x, ctx.gizmoProxy.position.y, ctx.gizmoProxy.position.z],
                snapAxis,
            );
            applyCellTranslate(ctx, cell, origin);
            showSnapMarker(ctx, target);
        } else if (st.gizmoMode === "rotate") {
            // Proxy euler is ZYX (see gizmoProxy.rotation.order) → matches the
            // store/compiler; snap to 0.1° so it reads cleanly in the panel.
            const e = ctx.gizmoProxy.rotation;
            const deg = (v: number) => Math.round(THREE.MathUtils.radToDeg(v) * 10) / 10;
            st.setCellRotation(cell.id, [deg(e.x), deg(e.y), deg(e.z)]);
        }
    });
}
