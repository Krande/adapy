/**
 * Cellbuilder UNDO/REDO slice.
 *
 * Owns: the past/future snapshot stacks, the transaction depth that coalesces a
 * burst of edits (a drag) into one step, and the `withHistory` wrapper every
 * model-mutating slice wraps its updater in. A snapshot is just the current
 * references of the undoable maps, so pushing one is cheap.
 */

import {pushSnapshot, redoStep, undoStep} from "@/utils/cellbuilder/history";
import type {BuilderCell, BuilderSelection, ModelSnapshot} from "./types";
import type {CellBuilderSet, CellBuilderSlice, CellBuilderState} from "./state";

/** How many model snapshots the undo stack keeps. */
export const HISTORY_LIMIT = 100;

export function snapshot(s: CellBuilderState): ModelSnapshot {
  return {
    cells: s.cells,
    loftMembers: s.loftMembers,
    systems: s.systems,
    blueprintOptions: s.blueprintOptions,
    equipmentCad: s.equipmentCad,
    designRules: s.designRules,
    groups: s.groups,
  };
}

/** After an undo/redo restores a snapshot, drop a selection pointing at a cell
 * that no longer exists. */
export function pruneSelection(
  sel: BuilderSelection | null,
  cells: Record<string, BuilderCell>,
): BuilderSelection | null {
  return sel && cells[sel.cellId] ? sel : null;
}

/** Wrap a model-mutating updater so it pushes the pre-change snapshot onto the
 * undo stack. Each slice binds one of these to its own `set`. */
export function makeWithHistory(set: CellBuilderSet) {
  // A transaction owns the snapshot for its whole burst of edits, so a
  // mutation inside one adds no entry of its own.
  const withHistory = (
    updater: (s: CellBuilderState) => Partial<CellBuilderState>,
  ) =>
    set((s) => {
      const partial = updater(s);
      // No-op updater (e.g. target cell gone) -> no state change, no history.
      if (!partial || Object.keys(partial).length === 0) return {};
      if (s.txDepth > 0) return partial;
      return { ...partial, ...pushSnapshot(s, snapshot(s), HISTORY_LIMIT) };
    });

  return withHistory;
}

export interface HistorySlice {
  /** Undo/redo history over the model state (cells/systems/blueprintOptions). */
  past: ModelSnapshot[];
  future: ModelSnapshot[];
  /** >0 while a coalesced edit (e.g. a face drag) is in progress — mutations
   * within don't push their own history entry. */
  txDepth: number;
  /** Restore the previous / next model snapshot. */
  undo: () => void;
  redo: () => void;
  /** Coalesce a burst of mutations (e.g. a face drag) into one undo step. */
  beginTransaction: () => void;
  endTransaction: () => void;
}

export const createHistorySlice: CellBuilderSlice<HistorySlice> = (set) => {
  return {
    past: [],
    future: [],
    txDepth: 0,
    undo: () =>
      set((s) => {
        const step = undoStep(s, snapshot(s), HISTORY_LIMIT);
        if (!step) return {};
        return {
          ...step.restored,
          ...step.stacks,
          // `open` and `commit` reset `past: []`, so an empty past means we're
          // back at the last save-point — i.e. no uncommitted changes. Undoing
          // all the way therefore clears dirty (Compile/Commit disable again),
          // instead of leaving the model perpetually dirty.
          dirty: step.stacks.past.length > 0,
          selection: pruneSelection(s.selection, step.restored.cells),
          selectedCellIds: s.selectedCellIds.filter(
            (id) => step.restored.cells[id],
          ),
        };
      }),
    redo: () =>
      set((s) => {
        const step = redoStep(s, snapshot(s), HISTORY_LIMIT);
        if (!step) return {};
        return {
          ...step.restored,
          ...step.stacks,
          dirty: step.stacks.past.length > 0,
          selection: pruneSelection(s.selection, step.restored.cells),
          selectedCellIds: s.selectedCellIds.filter(
            (id) => step.restored.cells[id],
          ),
        };
      }),
    beginTransaction: () =>
      set((s) => {
        // capture the pre-burst snapshot once, at the outermost begin
        if (s.txDepth === 0)
          return { txDepth: 1, ...pushSnapshot(s, snapshot(s), HISTORY_LIMIT) };
        return { txDepth: s.txDepth + 1 };
      }),
    endTransaction: () => set((s) => ({ txDepth: Math.max(0, s.txDepth - 1) })),

  };
};
