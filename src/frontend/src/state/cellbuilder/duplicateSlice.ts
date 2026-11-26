/**
 * Cellbuilder DUPLICATE slice.
 *
 * Owns: Shift+D -- copy the current selection in place (cells, loft members as a whole, a
 * plugin's refs through its own `duplicate` verb), select the copies and arm the move gizmo so a
 * drag places them; one undo step for the whole thing, even a bare keypress with nothing to drag
 * yet.
 */

import {nextFreeName} from "@/utils/cellbuilder/names";
import {builderRefId, builderRefProvider, type BuilderRef} from "./refsSlice";
import {regenLoftMemberCells} from "./loftCells";
import {nextId} from "./shared";
import type {CellBuilderSlice} from "./state";

export interface DuplicateSlice {
  /** Copy the current selection (selectedCellIds/selectedRefIds, or the lone primary pick when
   * neither multi-select set is populated) in place, select the copies and arm the translate
   * gizmo on them. Returns the copies' store keys (cell ids / "owner:key" ref ids) in no
   * particular order, or null when there was nothing to duplicate. */
  duplicateSelection: () => string[] | null;
}

/** `name` without a trailing `_NN` -- the stem `nextFreeName` numbers again for the copy. */
function nameStem(name: string): string {
  const m = /^(.*)_\d+$/.exec(name);
  return m ? m[1] : name;
}

export const createDuplicateSlice: CellBuilderSlice<DuplicateSlice> = (set, get) => {
  return {
    duplicateSelection: () => {
      const s0 = get();
      const cellIds = s0.selectedCellIds.length
        ? s0.selectedCellIds
        : s0.selection?.cellId
          ? [s0.selection.cellId]
          : [];
      const refIds = s0.selectedRefIds.length
        ? s0.selectedRefIds
        : s0.selection?.kind === "ref"
          ? [s0.selection.refId]
          : [];
      if (!cellIds.length && !refIds.length) return null;

      // A bare keypress with nothing ACTUALLY copyable (every cell id stale, and no selected
      // ref's own provider offers `duplicate`) must not open a transaction at all -- else it
      // adds an empty undo step and clears redo for nothing.
      const hasCell = cellIds.some((id) => s0.cells[id]);
      const hasRefDuplicate = refIds.some((id) => {
        const ref = s0.refs[id];
        return !!ref && !!builderRefProvider({owner: ref.owner})?.duplicate;
      });
      if (!hasCell && !hasRefDuplicate) return null;

      get().beginTransaction();
      try {
        const newCellIds: string[] = [];
        // A loft member is copied WHOLE (every bay) the first time any one of its selected bays
        // is reached; later bays of the same member are skipped (already copied).
        const copiedMembers = new Set<string>();
        set((s) => {
          let cells = s.cells;
          let loftMembers = s.loftMembers;
          for (const id of cellIds) {
            const cell = s.cells[id];
            if (!cell) continue;
            if (cell.kind === "loft" && cell.loft) {
              const memberName = cell.loft.member;
              if (copiedMembers.has(memberName)) continue;
              copiedMembers.add(memberName);
              const member = loftMembers.find((m) => m.NAME === memberName);
              if (!member) continue;
              const name = nextFreeLoftName(nameStem(memberName), loftMembers);
              const clone = {...member, NAME: name};
              cells = regenLoftMemberCells(cells, clone);
              loftMembers = [...loftMembers, clone];
              for (const c of Object.values(cells)) {
                if (c.kind === "loft" && c.loft?.member === name) newCellIds.push(c.id);
              }
              continue;
            }
            const newId = nextId();
            const name = nextFreeName(nameStem(cell.name), cells);
            cells = {...cells, [newId]: {...cell, id: newId, name}};
            newCellIds.push(newId);
          }
          if (cells === s.cells && loftMembers === s.loftMembers) return {};
          return {cells, loftMembers, dirty: true};
        });

        // Each provider's own refs, duplicated in one call per owner (a batch, not one call per
        // ref): a provider whose rows are keyed by position inserts each copy after its source,
        // so several per-ref calls would shift the keys from under each other.
        const newRefIds: string[] = [];
        const byOwner = new Map<string, {id: string; ref: BuilderRef}[]>();
        for (const id of refIds) {
          const ref = get().refs[id];
          if (!ref) continue;
          const list = byOwner.get(ref.owner) ?? [];
          list.push({id, ref});
          byOwner.set(ref.owner, list);
        }
        for (const [owner, entries] of byOwner) {
          const provider = builderRefProvider({owner});
          if (!provider?.duplicate) continue;
          const keys = provider.duplicate(entries.map((e) => e.ref));
          for (const key of keys ?? []) newRefIds.push(builderRefId(owner, key));
        }
        if (newRefIds.length) get().refreshRefs();

        const newIds = [...newCellIds, ...newRefIds];
        if (!newIds.length) return null;
        const primary = newCellIds.length ? newCellIds[newCellIds.length - 1] : newRefIds[newRefIds.length - 1];
        set({
          selection: newCellIds.length ? {kind: "cell", cellId: primary} : {kind: "ref", refId: primary},
          selectedCellIds: newCellIds,
          selectedRefIds: newRefIds,
          // Armed so the user places the copies with a drag; Esc leaves them where they landed
          // (in place, on top of the originals).
          gizmoMode: "translate",
          gizmoAxisLock: null,
          // The copy sits exactly on top of its original, so the "carry contained equipment"
          // rider rule must only ever pick up equipment that is ITSELF a copy -- never the
          // original's, which would otherwise also test as "inside" the copy's box and ride away
          // with it, leaving the original empty. Cleared once the selection moves on, or the
          // placing drag ends (cellGizmo.ts).
          placingCopyIds: newCellIds,
        });
        return newIds;
      } finally {
        get().endTransaction();
      }
    },
  };
};

/** `nextFreeName`'s own rule (the first free `_NN`), for loft member names instead of cells. */
function nextFreeLoftName(stem: string, members: readonly {NAME: string}[]): string {
  return nextFreeName(stem, members.map((m) => ({name: m.NAME})));
}
