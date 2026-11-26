/**
 * STRUCTURE FRAMES: where each structure's items are drawn and edited.
 *
 * Owns: the group per structure that the drawn items go in (under the cells', the
 * refs', the ports', the CAD previews' and the COG overlay's subgroups), placed at
 * the structure's origin; which structure the edit frame, the placement frame and
 * the port frame follow; and moving every frame when an origin changes. A structure's items are
 * stored relative to its origin (utils/cellbuilder/groups `CellGroup.origin`), so
 * moving a structure is a position write per frame, not a rebuild: the meshes and
 * the editing helpers keep their stored, structure-relative coordinates.
 *
 * Picking and box selection read world matrices, so they see the frames as drawn.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import type {BuilderCell} from "@/state/cellbuilder/types";
import type {BuilderRef} from "@/state/cellbuilder/refsSlice";
import {activeStructure, structureOrigin, type CellGroup} from "@/utils/cellbuilder/groups";
import {itemStructure} from "@/utils/cellbuilder/include";
import {quantize} from "@/utils/cellbuilder/snap";
import type {CellBuilderScene} from "./sceneContext";

type St = ReturnType<typeof useCellBuilderStore.getState>;
type StructureState = Pick<St, "cells" | "groups" | "loftMembers">;

/** The userData key a structure frame carries: its structure's name ("" for none). */
const FRAME_KEY = "__structureFrame";

const ADD_MODES: ReadonlySet<string> = new Set(["add-cell", "add-equipment", "add-opening"]);

/** The group under `parent` that structure `structure`'s items are drawn in (none: in no
 *  structure), made the first time, at the structure's origin. */
export const structureFrame = (
    parent: THREE.Object3D,
    structure: string | undefined,
    groups: readonly CellGroup[],
): THREE.Group => {
    const key = structure && structure !== "*" ? structure : "";
    let frame = parent.children.find((o) => o.userData[FRAME_KEY] === key) as THREE.Group | undefined;
    if (!frame) {
        frame = new THREE.Group();
        frame.name = `__structure__${key}`;
        frame.userData[FRAME_KEY] = key;
        parent.add(frame);
    }
    frame.position.set(...structureOrigin(groups, key));
    return frame;
};

/** The structure a cell (any kind) or a plugin's ref is in: a cell's by `itemStructure`, a ref's
 *  own (none for "*", every structure). Undefined for neither. */
export const boxStructure = (
    st: StructureState,
    box: {cell?: BuilderCell | null; ref?: BuilderRef | null},
): string | undefined => {
    if (box.ref) return box.ref.structure && box.ref.structure !== "*" ? box.ref.structure : undefined;
    if (!box.cell) return undefined;
    try {
        return itemStructure(box.cell, st.cells, st.groups, st.loftMembers);
    } catch {
        // A half-made cell (no box yet) is in no structure.
        return undefined;
    }
};

/** Where a cell's or a ref's coordinates are measured from: its structure's origin. */
export const boxOrigin = (
    st: StructureState,
    box: {cell?: BuilderCell | null; ref?: BuilderRef | null},
): readonly [number, number, number] => structureOrigin(st.groups, boxStructure(st, box));

/** The structure the edit frame follows: the selected item's; with nothing selected, the active one. */
export const editStructure = (st: St): string | undefined => {
    const sel = st.selection;
    if (sel?.kind === "ref") return boxStructure(st, {ref: st.refs[sel.refId]});
    const cell = sel?.cellId ? st.cells[sel.cellId] : undefined;
    return cell ? boxStructure(st, {cell}) : activeStructure(st);
};

/** The structure the placement frame (the ghost, the builder grid) follows: the active one's while
 *  an add mode places a new box -- which goes into it (placementSlice.addCell) -- else the edit
 *  frame's, for the numeric entries on the selected box (extrude, insert, opening). */
export const placeStructure = (st: St): string | undefined =>
    ADD_MODES.has(st.mode) ? activeStructure(st) : editStructure(st);

/** Move every frame to its structure's origin as the store has it now. */
export const syncStructureFrames = (ctx: CellBuilderScene): void => {
    const st = useCellBuilderStore.getState();
    for (const parent of [ctx.cellsGroup, ctx.refsGroup, ctx.portsGroup, ctx.cadPreviewGroup, ctx.cogGroup]) {
        for (const frame of parent.children) {
            const key = frame.userData[FRAME_KEY];
            if (typeof key === "string") frame.position.set(...structureOrigin(st.groups, key));
        }
    }
    ctx.editFrame.position.set(...structureOrigin(st.groups, editStructure(st)));
    ctx.placeFrame.position.set(...structureOrigin(st.groups, placeStructure(st)));
    const portCell = st.portGizmo ? st.cells[st.portGizmo.cellId] : undefined;
    ctx.portFrame.position.set(...(portCell ? boxOrigin(st, {cell: portCell}) : ([0, 0, 0] as const)));
};

// --- Moving a picked structure with the move gizmo (G) -------------------------------------------
// A structure is picked when a model is open, nothing in it is selected and a structure is active
// (the Model panel's structure row, the Properties' Structure section). G then puts the translate
// gizmo on it -- at the centre of its items' box, in its own frame -- and a drag moves the
// structure: its frames follow live (`moveStructureFrames`), and the release writes ONE
// `setGroupOrigin` (cellGizmo.ts), so a drag is one undo step.

/** The structure picked on its own, or undefined (an item is selected, or none is active). */
export const pickedStructure = (st: Pick<St, "active" | "selection" | "activeGroup" | "groups">): string | undefined =>
    st.active && st.selection === null ? activeStructure(st) : undefined;

/** Where the gizmo sits on structure `name`: the centre of its items' stored boxes (and its refs'),
 *  in its own frame; [0, 0, 0] (its origin) for a structure with nothing in it. */
export const structureGizmoCentre = (st: StructureState & Pick<St, "refs">, name: string): [number, number, number] => {
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    const add = (b: {origin: readonly number[]; size: readonly number[]}) => {
        for (let a = 0; a < 3; a++) {
            min[a] = Math.min(min[a], b.origin[a], b.origin[a] + b.size[a]);
            max[a] = Math.max(max[a], b.origin[a], b.origin[a] + b.size[a]);
        }
    };
    for (const cell of Object.values(st.cells)) if (boxStructure(st, {cell}) === name) add(cell);
    for (const ref of Object.values(st.refs)) if (ref.structure === name) add(ref);
    if (!Number.isFinite(min[0])) return [0, 0, 0];
    return [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
};

/** The structure's origin after its gizmo moved from `startCentre` to `centre`: only the moved
 *  `axes` change, each landing on the grid `step` (0: free, as with the left Shift key held). */
export const movedStructureOrigin = (
    start: readonly number[],
    startCentre: readonly number[],
    centre: readonly number[],
    axes: readonly (0 | 1 | 2)[],
    step: number,
): [number, number, number] => {
    const out: [number, number, number] = [start[0], start[1], start[2]];
    for (const a of axes) {
        const v = start[a] + (centre[a] - startCentre[a]);
        out[a] = quantize(v, step);
    }
    return out;
};

/** Draw structure `name` at `origin` while its gizmo drags it, ahead of the store: its frames under
 *  the cells, refs, ports and CAD previews. The edit frame (the gizmo's own) stays where it is. */
export const moveStructureFrames = (ctx: CellBuilderScene, name: string, origin: readonly [number, number, number]): void => {
    for (const parent of [ctx.cellsGroup, ctx.refsGroup, ctx.portsGroup, ctx.cadPreviewGroup, ctx.cogGroup]) {
        for (const frame of parent.children) if (frame.userData[FRAME_KEY] === name) frame.position.set(...origin);
    }
};
