/**
 * The box a builder GIZMO works on: a cell, or a plugin's ref (state/cellbuilder/refsSlice.ts).
 *
 * Owns: the one lookup the move gizmo, the axis-locked move, the resize handles and the face
 * drag share (`boxTarget`), and where each writes its result (`writeBox`): a cell is updated in
 * the store, a ref is handed to its plugin's `write`. Only these paths take a ref; everything
 * else in the builder still deals in cells.
 */

import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import type {BuilderRef} from "@/state/cellbuilder/refsSlice";
import type {CellBox, Vec3} from "@/utils/cellbuilder/snap";

/** A cell or a ref, as a box. `cell` is null for a ref, `ref` null for a cell. */
export interface GizmoBox {
    id: string;
    origin: Vec3;
    size: Vec3;
    cell: BuilderCell | null;
    ref: BuilderRef | null;
}

type St = Pick<ReturnType<typeof useCellBuilderStore.getState>, "cells" | "refs" | "selection">;

/** The cell `id`, or with `isRef` the ref `id`; null when it is gone. */
export const boxTarget = (st: St, id: string, isRef = false): GizmoBox | null => {
    if (isRef) {
        const ref = st.refs[id];
        return ref ? {id, origin: ref.origin, size: ref.size, cell: null, ref} : null;
    }
    const cell = st.cells[id];
    return cell ? {id, origin: cell.origin, size: cell.size, cell, ref: null} : null;
};

/** The selected cell or ref; null when nothing is selected. */
export const selectedBox = (st: St): GizmoBox | null => {
    const sel = st.selection;
    if (!sel) return null;
    return sel.kind === "ref" ? boxTarget(st, sel.refId, true) : boxTarget(st, sel.cellId);
};

/** Whether the gizmo may move `box`: every cell, a ref whose plugin allows it. */
export const boxMoves = (box: GizmoBox): boolean => (box.ref ? box.ref.edit.move : true);

/** Write `next` to `box`: a cell's own box, or the ref's plugin's `write`. */
export const writeBox = (box: Pick<GizmoBox, "id" | "ref">, next: CellBox): void => {
    const st = useCellBuilderStore.getState();
    if (box.ref) st.writeRef(box.id, {origin: [...next.origin] as Vec3, size: [...next.size] as Vec3});
    else st.updateCell(box.id, {origin: [...next.origin] as Vec3, size: [...next.size] as Vec3});
};
