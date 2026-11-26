/**
 * Cell TRANSLATE/ROTATE gizmo and the axis-locked modal move.
 *
 * Owns: the TransformControls proxy that drives the selected cell, the
 * Blender-style modal move (lock an axis, the cell tracks the pointer, click
 * confirms / Escape cancels), the move measure (how far the moved cell has
 * come, drawn by cellDimensions.ts), pointer-driven vertex magnetism with its
 * on-screen marker, and carrying a space cell's contained equipment along.
 */

import * as THREE from "three";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {boxMoves, boxTarget, selectedBox, writeBox, type GizmoBox} from "./gizmoBox";
import {requestRender} from "@/state/perfStore";
import {axesOfHandle, boxCorners, movedOrigin, quantize, type CellBox, type Vec3} from "@/utils/cellbuilder/snap";
import {gizmoMoveStep} from "@/utils/gizmo/transformKeys";
import {freeMove, snapStep} from "@/utils/gizmo/freeMove";
import {HANDLE_AXIS_COLOR, SNAP_PX} from "./sceneConstants";
import {offsetVec, type CellBuilderScene, type FrameAt} from "./sceneContext";
import {boxOrigin, boxStructure, movedStructureOrigin, moveStructureFrames, pickedStructure} from "./structureFrames";
import {structureOrigin} from "@/utils/cellbuilder/groups";
import {builderCamera} from "./viewHost";
import {boxFrameMatrix} from "@/utils/cellbuilder/cellResize";
import {syncMoveMeasure} from "./cellDimensions";
import {movableSet, movableWorldCentre, multiMoveActive} from "./multiMove";

// --- Move measure ----------------------------------------------------------
// While a cell (or a plugin's ref) is moved (a gizmo drag or the axis-locked modal
// move), dimension lines show how far it is from where the move began
// (cellDimensions.ts). The start is captured when the move begins and dropped
// when it ends or is cancelled; syncGizmo redraws the lines on every cell change,
// so they follow the snapped position live.
export const beginMoveMeasure = (ctx: CellBuilderScene, box: GizmoBox) => {
    // A loft band moves its whole member, not this box: not measured.
    if (box.cell?.kind === "loft") return;
    // A new leg of the same grab (another axis lock) keeps measuring from the first start.
    if (ctx.moveMeasure?.cellId === box.id) return;
    ctx.moveMeasure = {cellId: box.id, ref: !!box.ref, start: {origin: [...box.origin], size: [...box.size]}};
};

export const endMoveMeasure = (ctx: CellBuilderScene) => {
    ctx.moveMeasure = null;
    syncMoveMeasure(ctx, null, null);
};

/** Draw the move measure for the current state, or clear it once nothing is being moved. */
export const reconcileMoveMeasure = (ctx: CellBuilderScene) => {
    const mm = ctx.moveMeasure;
    if (!mm) return syncMoveMeasure(ctx, null, null);
    const st = useCellBuilderStore.getState();
    const box = boxTarget(st, mm.cellId, mm.ref);
    if (!box || !st.active || st.gizmoMode !== "translate" || selectedBox(st)?.id !== mm.cellId) {
        return endMoveMeasure(ctx);
    }
    // A rotated equipment's start outline is turned as the box is (moving never rotates it).
    const cell = box.cell;
    syncMoveMeasure(ctx, mm.start, box, boxFrameMatrix(mm.start, cell?.kind === "equipment" ? cell.rotation : null));
};


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

export const cellCenterModel = (ctx: CellBuilderScene, cell: CellBox): Vec3 => [
    cell.origin[0] + cell.size[0] / 2,
    cell.origin[1] + cell.size[1] / 2,
    cell.origin[2] + cell.size[2] / 2,
];

// Equipment cells whose CENTRE sits inside `cell`'s box (all 3 axes, so the
// two floors of a stacked model don't grab each other's units) — these ride
// along when the space cell is translated. Captured once at drag start.
// Only its own structure's: another structure's numbers are in another frame.
export const equipContainedIn = (ctx: CellBuilderScene, cell: BuilderCell): string[] => {
    const st = useCellBuilderStore.getState();
    if (cell.kind !== "cell" || !st.moveEquipWithCell) return [];
    const [ox, oy, oz] = cell.origin;
    const [dx, dy, dz] = cell.size;
    // A duplicate (Shift+D) lands exactly on top of its original, so the original's own
    // equipment also tests as "centre inside" the copy's box. While `placingCopyIds` names the
    // just-placed copies, only equipment that is ITSELF one of them may ride along.
    const placing = st.placingCopyIds;
    const ids: string[] = [];
    for (const c of Object.values(st.cells)) {
        if (c.kind !== "equipment" || boxStructure(st, {cell: c}) !== cell.group) continue;
        if (placing && !placing.includes(c.id)) continue;
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
// Show the marker at a snapped neighbour vertex (in the frame `at`, by default
// the edit frame: the moved item's), or hide it.
export const showSnapMarker = (ctx: CellBuilderScene, targetModel: Vec3 | null, at?: FrameAt) => {
    if (!targetModel) {
        if (ctx.snapMarker.visible) {
            ctx.snapMarker.visible = false;
            requestRender();
        }
        return;
    }
    const off = offsetVec(ctx, at);
    ctx.snapMarker.position.set(targetModel[0] + off.x, targetModel[1] + off.y, targetModel[2] + off.z);
    ctx.snapMarker.visible = true;
    requestRender();
};


// The neighbour-cell corner nearest the pointer on screen, within SNAP_PX, or
// null. This is the snap TARGET — the vertex under the cursor — so the marker
// lands exactly where the user is pointing. Each cell is where it is drawn (in
// its structure's frame); the target is given in the edit frame, the moved
// item's, so a box snaps onto another structure's corner as it is seen.
export const nearestCornerToPointer = (ctx: CellBuilderScene, excludeCellId: string): Vec3 | null => {
    const cam = builderCamera(ctx);
    const st = useCellBuilderStore.getState();
    const here = ctx.editFrame.position;
    const rect = ctx.host.rect();
    const px =(ctx.pointer.x * 0.5 + 0.5) * rect.width;
    const py = (-ctx.pointer.y * 0.5 + 0.5) * rect.height;
    const v = new THREE.Vector3();
    let best: Vec3 | null = null;
    let bestD = SNAP_PX;
    for (const c of Object.values(st.cells)) {
        if (c.id === excludeCellId) continue;
        const o = boxOrigin(st, {cell: c});
        const off = offsetVec(ctx, o);
        for (const corner of boxCorners({origin: c.origin, size: c.size})) {
            v.set(corner[0] + off.x, corner[1] + off.y, corner[2] + off.z).project(cam);
            if (v.z < -1 || v.z > 1) continue; // behind camera / clipped
            const sx = (v.x * 0.5 + 0.5) * rect.width;
            const sy = (-v.y * 0.5 + 0.5) * rect.height;
            const d = Math.hypot(sx - px, sy - py);
            if (d <= bestD) {
                bestD = d;
                best = [corner[0] + o[0] - here.x, corner[1] + o[1] - here.y, corner[2] + o[2] - here.z];
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
// modal move) — a plane/centre drag (several `axes`) would otherwise jump the
// cell out of its drag plane. No target under the cursor ⇒ grid-quantize.
// Only the moved `axes` change (and snap): the others keep the cell's own
// values exactly. While the left Shift key is held (free move) nothing snaps.
export const computeMove = (
    ctx: CellBuilderScene,
    cell: {id: string; origin: Vec3; size: Vec3},
    center: Vec3,
    axes: readonly (0 | 1 | 2)[],
): {origin: Vec3; target: Vec3 | null} => {
    const st = useCellBuilderStore.getState();
    const step = snapStep(gizmoMoveStep(st.gridStep));
    if (st.gizmoVertexSnap && axes.length === 1 && !freeMove()) {
        const axis = axes[0];
        const target = nearestCornerToPointer(ctx, cell.id);
        if (target) {
            // Snap the near-or-far face depending on which the cursor is by:
            // pick whichever of the box's two faces along `axis` is closer to
            // the target's axis coordinate. The face lands ON the vertex.
            const near = center[axis] - cell.size[axis] / 2; // low face
            const far = near + cell.size[axis]; // high face
            const alignLow = Math.abs(target[axis] - near) <= Math.abs(target[axis] - far);
            const origin: Vec3 = [...cell.origin];
            origin[axis] = alignLow ? target[axis] : target[axis] - cell.size[axis];
            return {origin, target};
        }
    }
    return {origin: movedOrigin(cell.origin, cell.size, center, axes, step), target: null};
};

export const startModalMove = (ctx: CellBuilderScene, cell: GizmoBox, axis: 0 | 1 | 2) => {
    // Switching axis/cell mid-grab: close the previous leg's undo step first
    // (keeping its position — a plain re-lock is a confirm, not a cancel).
    if (ctx.modalMove && ctx.modalMove.startT !== null) useCellBuilderStore.getState().endTransaction();
    const centerModel = cellCenterModel(ctx, cell);
    ctx.modalMove = {
        cellId: cell.id,
        ref: !!cell.ref,
        axis,
        lineDir: new THREE.Vector3(axis === 0 ? 1 : 0, axis === 1 ? 1 : 0, axis === 2 ? 1 : 0),
        lineOrigin: new THREE.Vector3(centerModel[0], centerModel[1], centerModel[2]).add(offsetVec(ctx)),
        startT: null,
        startBox: {origin: [...cell.origin], size: [...cell.size]},
    };
    beginMoveMeasure(ctx, cell);
    showGuideLine(ctx, axis, centerModel);
    ctx.host.input().style.cursor = "move";
};

export const endModalMove = (ctx: CellBuilderScene, cancel: boolean) => {
    if (!ctx.modalMove) return;
    const mm = ctx.modalMove;
    ctx.modalMove = null;
    if (mm.startT !== null) {
        if (cancel && mm.ref) {
            // A ref goes back through its plugin, as it was moved.
            writeBox({id: mm.cellId, ref: useCellBuilderStore.getState().refs[mm.cellId] ?? null}, mm.startBox);
        } else if (cancel) {
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
    endMoveMeasure(ctx);
    ctx.gizmo.enabled = true;
    ctx.guideLine.visible = false;
    ctx.snapMarker.visible = false;
    ctx.host.input().style.cursor = "";
    requestRender();
};

// Reconcile the modal-move with the store: active whenever the translate
// gizmo is locked to an axis on a (non-loft) cell, or on a ref its plugin lets
// move. Called at the end of syncGizmo, so it can re-hide the gizmo helper that
// syncGizmo just showed.
export const reconcileModalMove = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const cell = selectedBox(st);
    const on = !!(
        st.active &&
        st.gizmoMode === "translate" &&
        st.gizmoAxisLock !== null &&
        cell &&
        cell.cell?.kind !== "loft" &&
        boxMoves(cell) &&
        st.cellsVisible &&
        // A multi-item drag still has a PRIMARY pick (`cell` above), but it moves
        // through the ordinary gizmo (resizeHandles.ts's multiOn), never this single-item modal
        // move -- which would otherwise disable the widget out from under the other items.
        // `multiMoveActive`, not a plain `movableCount(...) < 2`: see multiMove.ts for why.
        !multiMoveActive(st)
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
        ctx.host.setCameraEnabled(!e.value);
        // Coalesce the whole widget drag into one undo step.
        if (e.value) {
            st.beginTransaction();
            // Seed the loft member-move baseline at the proxy's current (box-
            // centre) position; cleared when the drag ends.
            ctx.loftDragLast = ctx.gizmoProxy.position.clone();
            // 2+ selected items win outright -- checked first, and NOT gated on "no
            // primary selection" (`box` below), since a multi-selection still has one (the most
            // recently picked item drives the detail editors; selectedBox/box reads it, same as a
            // single pick). The proxy is already at the movable set's world centre (syncGizmo
            // seeded it there).
            const multiSet = st.gizmoMode === "translate" ? movableSet(st) : null;
            // `multiMoveActive`, not a plain `movableCount(multiSet) >= 2`: see
            // multiMove.ts for why.
            if (multiSet && multiMoveActive(st)) {
                const p = ctx.gizmoProxy.position;
                // Write spaces first, then equipment, then openings, then refs -- never
                // plain selection order. A global opening re-homes against its space on every
                // `updateCell` (cellsSlice.ts); writing the space AFTER its opening in the same
                // tick would re-home the opening against the space's PREVIOUS-tick position.
                const openingKindRank = (item: {kind: "cell" | "ref"; id: string}): number => {
                    if (item.kind === "ref") return 3;
                    const k = st.cells[item.id]?.kind;
                    return k === "cell" ? 0 : k === "opening" ? 2 : 1; // equipment (or anything else): 1
                };
                const entries: {kind: "cell" | "ref"; id: string; start: Vec3}[] = [];
                for (const item of [...multiSet.entries, ...multiSet.riderIds.map((id) => ({kind: "cell" as const, id}))].sort(
                    (a, b) => openingKindRank(a) - openingKindRank(b),
                )) {
                    const start = item.kind === "cell" ? st.cells[item.id]?.origin : st.refs[item.id]?.origin;
                    if (start) entries.push({kind: item.kind, id: item.id, start: [...start] as Vec3});
                }
                ctx.multiMove = {
                    startCentre: [p.x, p.y, p.z],
                    entries,
                    loftApplied: new Map(multiSet.loftMembers.map((m) => [m, [0, 0, 0] as Vec3])),
                };
                if (multiSet.blockedRefIds.length)
                    st.setToolHint(`${multiSet.blockedRefIds.length} item${multiSet.blockedRefIds.length > 1 ? "s" : ""} not moved`);
                ctx.translateEquip = [];
            } else {
                // Capture the equipment sitting in the cell so they ride along.
                const box = selectedBox(st);
                ctx.translateEquip = box?.cell && st.gizmoMode === "translate" ? equipContainedIn(ctx, box.cell) : [];
                if (box && st.gizmoMode === "translate") beginMoveMeasure(ctx, box);
                // A structure picked on its own: the drag moves the whole structure.
                const structure = !box && st.gizmoMode === "translate" ? pickedStructure(st) : undefined;
                if (structure !== undefined) {
                    const start = [...structureOrigin(st.groups, structure)] as Vec3;
                    const p = ctx.gizmoProxy.position;
                    ctx.structureDrag = {name: structure, start, startCentre: [p.x, p.y, p.z], origin: [...start] as Vec3};
                }
            }
        } else {
            // The structure's new place goes to the store once, at the release: one undo step.
            const sd = ctx.structureDrag;
            ctx.structureDrag = null;
            if (sd && sd.origin.some((v, i) => v !== sd.start[i])) st.setGroupOrigin(sd.name, sd.origin);
            if (ctx.multiMove) {
                ctx.multiMove = null;
                st.setToolHint(null);
            }
            st.endTransaction();
            ctx.loftDragLast = null;
            ctx.translateEquip = [];
            // The copy this drag was placing has now been placed -- the next drag (even one
            // that picks the original space back up) must not have its equipment riders narrowed
            // to the copy's ids again.
            if (st.placingCopyIds) st.setPlacingCopyIds(null);
            showSnapMarker(ctx, null); // drag ended — clear the snap indicator
            endMoveMeasure(ctx);
        }
        requestRender();
    });
    ctx.gizmo.addEventListener("objectChange", () => {
        const st = useCellBuilderStore.getState();
        // The multi-item drag, checked first and NOT gated on "no primary selection"
        // (a multi-selection still has one; see the dragging-changed handler's own comment).
        // Every entry's new ABSOLUTE origin is the same math a picked structure's own drag already
        // uses (movedStructureOrigin): its own start, this group's start/current centre, the axes
        // actually dragged. A loft member moves incrementally (moveLoftMember takes a delta): the
        // group's quantized delta so far, minus what was already applied to it.
        const mm = ctx.multiMove;
        if (mm) {
            const handle = (ctx.gizmo as unknown as {axis: string | null}).axis;
            const axes = st.gizmoAxisLock !== null ? [st.gizmoAxisLock as 0 | 1 | 2] : axesOfHandle(handle);
            const p = ctx.gizmoProxy.position;
            const centre: Vec3 = [p.x, p.y, p.z];
            const step = snapStep(gizmoMoveStep(st.gridStep));
            // The SHARED delta is quantized once here -- not each item's own absolute
            // start+delta (movedStructureOrigin's single-item rule), which would round items that
            // are off the grid (refs especially) by different amounts and spread an otherwise rigid
            // group apart. Every entry then gets the identical delta on the dragged axes, its other
            // axes untouched.
            const groupDelta: Vec3 = [0, 0, 0];
            for (const a of axes) groupDelta[a] = step > 0 ? quantize(centre[a] - mm.startCentre[a], step) : centre[a] - mm.startCentre[a];
            for (const entry of mm.entries) {
                const origin: Vec3 = [...entry.start];
                for (const a of axes) origin[a] = entry.start[a] + groupDelta[a];
                if (entry.kind === "ref") {
                    const ref = st.refs[entry.id];
                    if (ref) writeBox({id: entry.id, ref}, {origin, size: ref.size});
                } else {
                    const cell = st.cells[entry.id];
                    if (cell) st.updateCell(entry.id, {origin});
                }
            }
            for (const [member, applied] of mm.loftApplied) {
                const tick: Vec3 = [groupDelta[0] - applied[0], groupDelta[1] - applied[1], groupDelta[2] - applied[2]];
                if (tick[0] || tick[1] || tick[2]) {
                    st.moveLoftMember(member, tick);
                    mm.loftApplied.set(member, [...groupDelta] as Vec3);
                }
            }
            requestRender();
            return;
        }
        const box = selectedBox(st);
        // A picked structure: its frames follow the gizmo live, the origin on the grid along the
        // dragged axes only (free with the left Shift key held); the store is written on release.
        const sd = ctx.structureDrag;
        if (!box && sd) {
            const handle = (ctx.gizmo as unknown as {axis: string | null}).axis;
            const axes = st.gizmoAxisLock !== null ? [st.gizmoAxisLock as 0 | 1 | 2] : axesOfHandle(handle);
            const p = ctx.gizmoProxy.position;
            sd.origin = movedStructureOrigin(sd.start, sd.startCentre, [p.x, p.y, p.z], axes, snapStep(gizmoMoveStep(st.gridStep)));
            moveStructureFrames(ctx, sd.name, sd.origin);
            requestRender();
            return;
        }
        if (!box) return;
        // A ref moves through its plugin, snapped as a cell is (only the dragged axes); it never turns.
        if (box.ref) {
            if (st.gizmoMode !== "translate" || !box.ref.edit.move) return;
            const handle = (ctx.gizmo as unknown as {axis: string | null}).axis;
            const axes = st.gizmoAxisLock !== null ? [st.gizmoAxisLock as 0 | 1 | 2] : axesOfHandle(handle);
            const center: Vec3 = [ctx.gizmoProxy.position.x, ctx.gizmoProxy.position.y, ctx.gizmoProxy.position.z];
            const {origin, target} = computeMove(ctx, box, center, axes);
            writeBox(box, {origin, size: box.size});
            showSnapMarker(ctx, target);
            return;
        }
        const cell = box.cell;
        if (!cell) return;
        if (st.gizmoMode === "translate") {
            const step = gizmoMoveStep(st.gridStep);
            // Loft band: move the WHOLE member (not this one bay) by a grid-
            // quantized incremental delta. loftDragLast tracks the applied
            // position so the member steps in exact grid multiples with no drift.
            if (cell.kind === "loft" && cell.loft) {
                if (!ctx.loftDragLast) ctx.loftDragLast = ctx.gizmoProxy.position.clone();
                // Free move (left Shift held): the raw delta, no steps.
                const stepped = (d: number) => (freeMove() ? d : Math.round(d / step) * step);
                const dx = stepped(ctx.gizmoProxy.position.x - ctx.loftDragLast.x);
                const dy = stepped(ctx.gizmoProxy.position.y - ctx.loftDragLast.y);
                const dz = stepped(ctx.gizmoProxy.position.z - ctx.loftDragLast.z);
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
            // Only the dragged axes move: an X arrow leaves Y and Z as they were.
            const handle = (ctx.gizmo as unknown as {axis: string | null}).axis;
            const axes = st.gizmoAxisLock !== null ? [st.gizmoAxisLock as 0 | 1 | 2] : axesOfHandle(handle);
            const {origin, target} = computeMove(ctx,
                cell,
                [ctx.gizmoProxy.position.x, ctx.gizmoProxy.position.y, ctx.gizmoProxy.position.z],
                axes,
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
