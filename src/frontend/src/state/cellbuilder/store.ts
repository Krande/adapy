/**
 * The cellbuilder STORE — one zustand store composed from the domain slices.
 *
 * Owns nothing itself: it hands each slice creator the same `set`/`get` and
 * merges their objects, so `useCellBuilderStore.getState()` returns one flat
 * state with every field and action, unchanged from before the split. Slice
 * order is irrelevant — no key is defined twice.
 */

import {create} from "zustand";

import type {CellBuilderState} from "./state";
import {createDocumentSlice} from "./documentSlice";
import {createHistorySlice} from "./historySlice";
import {createSelectionSlice} from "./selectionSlice";
import {createCellsSlice} from "./cellsSlice";
import {createPlacementSlice} from "./placementSlice";
import {createLoftSlice} from "./loftSlice";
import {createSystemsSlice} from "./systemsSlice";
import {createCatalogsSlice} from "./catalogsSlice";
import {createDetailingSlice} from "./detailingSlice";
import {createBlueprintsSlice} from "./blueprintsSlice";
import {createJobsSlice} from "./jobsSlice";
import {createViewStateSlice} from "./viewStateSlice";
import {createRelocationsSlice} from "./relocationsSlice";
import {createExportImportSlice} from "./exportImportSlice";
import {createRefsSlice} from "./refsSlice";
import {itemStructure} from "@/utils/cellbuilder/include";

export const useCellBuilderStore = create<CellBuilderState>()((...a) => ({
  ...createDocumentSlice(...a),
  ...createHistorySlice(...a),
  ...createSelectionSlice(...a),
  ...createCellsSlice(...a),
  ...createPlacementSlice(...a),
  ...createLoftSlice(...a),
  ...createSystemsSlice(...a),
  ...createCatalogsSlice(...a),
  ...createDetailingSlice(...a),
  ...createBlueprintsSlice(...a),
  ...createJobsSlice(...a),
  ...createViewStateSlice(...a),
  ...createRelocationsSlice(...a),
  ...createExportImportSlice(...a),
  ...createRefsSlice(...a),
}));

// Picking a structure's item makes that structure the active one (a new space
// goes into it: placementSlice.addCell). An item in no structure leaves it be.
useCellBuilderStore.subscribe((s, prev) => {
  const sel = s.selection;
  // A ref names its structure itself; "*" (every structure) leaves the active one be.
  if (sel?.kind === "ref") {
    if (sel === prev.selection) return;
    const structure = s.refs[sel.refId]?.structure;
    if (structure && structure !== "*" && structure !== s.activeGroup && s.groups.some((g) => g.name === structure)) {
      useCellBuilderStore.setState({ activeGroup: structure });
    }
    return;
  }
  const id = sel?.cellId;
  if (!id || id === prev.selection?.cellId) return;
  const cell = s.cells[id];
  let name: string | undefined;
  try {
    name = cell ? itemStructure(cell, s.cells, s.groups, s.loftMembers) : undefined;
  } catch {
    // A half-made cell (no box yet) is in no structure: a listener must not fail the store's set.
    return;
  }
  if (name && name !== s.activeGroup) useCellBuilderStore.setState({ activeGroup: name });
});

// The plugins' refs follow the doc's extras and the cells: projected again on a change of
// either (an edit, an undo, a model opened), and a pick of a ref that went is dropped.
useCellBuilderStore.subscribe((s, prev) => {
  if (s.docExtras !== prev.docExtras || s.cells !== prev.cells) s.refreshRefs();
});
