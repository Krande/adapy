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
}));
