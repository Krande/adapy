/**
 * Multi-selection TRANSLATE: G on 2+ selected items moves them all together, as
 * one undo step.
 *
 * Owns: the pure "movable set" rules (what actually moves, what rides along for free, what
 * cannot move at all) and the world-space centre they are dragged from. The actual per-tick
 * writes are cellGizmo.ts's: each item keeps the SAME math a picked structure's own G already
 * uses (structureFrames.ts `movedStructureOrigin`) -- structures only translate, so one world
 * delta applies in every item's own frame unchanged.
 */

import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import type {Vec3} from "@/utils/cellbuilder/snap";
import {structureOrigin} from "@/utils/cellbuilder/groups";
import {boxStructure} from "./structureFrames";

type St = Pick<
    ReturnType<typeof useCellBuilderStore.getState>,
    "cells" | "refs" | "groups" | "loftMembers" | "selectedCellIds" | "selectedRefIds" | "moveEquipWithCell" | "placingCopyIds"
>;

/** Equipment cells whose centre sits inside `cell`'s box, in its own structure only -- the same
 *  rule a single selected space's G already carries its equipment by (cellGizmo.ts's
 *  `equipContainedIn`; duplicated here, pure and ctx-free, so a multi-selection with SEVERAL
 *  selected spaces can gather each one's riders without a scene context).
 *
 *  A duplicate (Shift+D) is placed exactly on top of its original, so right after it is
 *  made every one of the original's own equipment also tests as "centre inside" the copy's box.
 *  While `st.placingCopyIds` names the just-placed copies, only equipment that is ITSELF one of
 *  them may ride along -- the original's equipment stays with the original. */
function equipmentInside(st: St, cell: BuilderCell): string[] {
    const [ox, oy, oz] = cell.origin;
    const [dx, dy, dz] = cell.size;
    const placing = st.placingCopyIds;
    const ids: string[] = [];
    for (const c of Object.values(st.cells)) {
        if (c.kind !== "equipment" || boxStructure(st, {cell: c}) !== cell.group) continue;
        if (placing && !placing.includes(c.id)) continue;
        const cx = c.origin[0] + c.size[0] / 2;
        const cy = c.origin[1] + c.size[1] / 2;
        const cz = c.origin[2] + c.size[2] / 2;
        if (cx >= ox && cx <= ox + dx && cy >= oy && cy <= oy + dy && cz >= oz && cz <= oz + dz) ids.push(c.id);
    }
    return ids;
}

/** One plain cell or ref the group moves as itself. */
export interface MovableEntry {
    kind: "cell" | "ref";
    id: string;
}

export interface MovableSet {
    /** Plain cells (space/equipment/opening) and refs, each moved as itself. */
    entries: MovableEntry[];
    /** Loft MEMBER names with one or more selected bay -- a loft moves as a whole, never one bay
     *  (duplicate bays of the same member collapse to one entry here). */
    loftMembers: string[];
    /** Equipment riding along with a selected space (moveEquipWithCell): moved by the same delta,
     *  but not independently counted towards `movableCount` -- it is "free" for whichever space
     *  carries it, exactly as a single selected space's own G already carries it. */
    riderIds: string[];
    /** Selected refs whose plugin does not allow a move: left exactly where they are. */
    blockedRefIds: string[];
}

/** The movable set for the CURRENT selection: `selectedCellIds` plus `selectedRefIds`. Call this,
 *  not `movableCount(movableSet(st)) >= 2` ad hoc, everywhere the multi-item G is gated or driven --
 *  one rule, shared by the keyboard shortcut, the gizmo sync and the drag handlers. */
export function movableSet(st: St): MovableSet {
    const selectedCells = st.selectedCellIds
        .map((id) => st.cells[id])
        .filter((c): c is BuilderCell => !!c);
    const riderIds = new Set<string>();
    if (st.moveEquipWithCell) {
        for (const c of selectedCells) if (c.kind === "cell") for (const id of equipmentInside(st, c)) riderIds.add(id);
    }
    const entries: MovableEntry[] = [];
    const loftMembers: string[] = [];
    const seenMembers = new Set<string>();
    for (const c of selectedCells) {
        if (riderIds.has(c.id)) continue; // moves for free with its space
        if (c.kind === "loft" && c.loft) {
            if (!seenMembers.has(c.loft.member)) {
                seenMembers.add(c.loft.member);
                loftMembers.push(c.loft.member);
            }
            continue;
        }
        entries.push({kind: "cell", id: c.id});
    }
    const blockedRefIds: string[] = [];
    for (const id of st.selectedRefIds) {
        const ref = st.refs[id];
        if (!ref) continue;
        if (ref.edit.move) entries.push({kind: "ref", id});
        else blockedRefIds.push(id);
    }
    return {entries, loftMembers, riderIds: [...riderIds], blockedRefIds};
}

/** How many INDEPENDENT things `set` would move: riders ride for free (they don't add to the
 *  count), and a loft counts once per member, however many of its bays are selected. See
 *  `multiMoveActive` for whether 2+ SELECTED items actually arms the multi-item G: that is not
 *  simply "this is 2 or more" -- a selection can be 2+ items with only one of them able to
 *  move independently (the rest ride for free, or cannot move at all) and still belongs on the
 *  multi path, not fall back to the single-item path on whichever happens to be primary. */
export const movableCount = (set: MovableSet): number => set.entries.length + set.loftMembers.length;

/** Whether G should arm the MULTI-item gizmo for the current selection: 2 or more items
 *  are actually SELECTED (cells and/or refs -- riders are not separately selected, so they are
 *  not counted here either) and at least one of them can actually move. This is deliberately
 *  looser than `movableCount(movableSet(st)) >= 2`: a selected space plus its own (also selected)
 *  equipment is 2 selections but 1 movable entry (the equipment rides for free) -- the single-item
 *  path on whichever is primary would otherwise move only that one, dropping the other; a selected
 *  space plus a support that cannot move is the same shape (1 movable, 1 blocked) -- G must still
 *  arm so the move happens and the blocked item's hint can show, rather than G doing nothing. */
export function multiMoveActive(st: St): boolean {
    return st.selectedCellIds.length + st.selectedRefIds.length >= 2 && movableCount(movableSet(st)) >= 1;
}

/** The world-space centre of everything `set` would move (its entries, its riders and its loft
 *  members' own bays) -- each box in ITS OWN structure's frame, offset to world. [0, 0, 0] for an
 *  empty set (never shown: the caller gates on `movableCount(set) >= 2`). */
export function movableWorldCentre(st: St, set: MovableSet): Vec3 {
    const min: Vec3 = [Infinity, Infinity, Infinity];
    const max: Vec3 = [-Infinity, -Infinity, -Infinity];
    const add = (origin: readonly number[], size: readonly number[], off: readonly number[]) => {
        for (let a = 0; a < 3; a++) {
            min[a] = Math.min(min[a], origin[a] + off[a], origin[a] + size[a] + off[a]);
            max[a] = Math.max(max[a], origin[a] + off[a], origin[a] + size[a] + off[a]);
        }
    };
    const addCell = (c: BuilderCell) => add(c.origin, c.size, structureOrigin(st.groups, boxStructure(st, {cell: c})));
    for (const e of set.entries) {
        if (e.kind === "cell") {
            const c = st.cells[e.id];
            if (c) addCell(c);
        } else {
            const ref = st.refs[e.id];
            if (ref) add(ref.origin, ref.size, structureOrigin(st.groups, boxStructure(st, {ref})));
        }
    }
    for (const member of set.loftMembers) {
        for (const c of Object.values(st.cells)) if (c.kind === "loft" && c.loft?.member === member) addCell(c);
    }
    for (const id of set.riderIds) {
        const c = st.cells[id];
        if (c) addCell(c);
    }
    if (!Number.isFinite(min[0])) return [0, 0, 0];
    return [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
}
