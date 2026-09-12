/**
 * The COMPOSED cellbuilder state type.
 *
 * Owns: the intersection of every slice interface (`CellBuilderState`) and the
 * slice-creator / set / get aliases the slices and job runners are typed
 * against. Type-only — nothing here exists at runtime, so a slice importing it
 * back creates no module cycle.
 */

import type {StateCreator} from "zustand";

import type {DocumentSlice} from "./documentSlice";
import type {HistorySlice} from "./historySlice";
import type {SelectionSlice} from "./selectionSlice";
import type {CellsSlice} from "./cellsSlice";
import type {PlacementSlice} from "./placementSlice";
import type {LoftSlice} from "./loftSlice";
import type {SystemsSlice} from "./systemsSlice";
import type {CatalogsSlice} from "./catalogsSlice";
import type {DetailingSlice} from "./detailingSlice";
import type {BlueprintsSlice} from "./blueprintsSlice";
import type {JobsSlice} from "./jobsSlice";
import type {ViewStateSlice} from "./viewStateSlice";
import type {RelocationsSlice} from "./relocationsSlice";
import type {ExportImportSlice} from "./exportImportSlice";

/** The composed cellbuilder store: every slice's state and actions in one
 * object, exactly the shape `useCellBuilderStore.getState()` has always
 * returned. Slices cross-call through the composed `get()`, never by importing
 * one another. */
export type CellBuilderState =
  DocumentSlice
  & HistorySlice
  & SelectionSlice
  & CellsSlice
  & PlacementSlice
  & LoftSlice
  & SystemsSlice
  & CatalogsSlice
  & DetailingSlice
  & BlueprintsSlice
  & JobsSlice
  & ViewStateSlice
  & RelocationsSlice
  & ExportImportSlice;

/** A slice creator: sees the WHOLE store through `set`/`get`, contributes `T`. */
export type CellBuilderSlice<T> = StateCreator<CellBuilderState, [], [], T>;

type SliceArgs = Parameters<CellBuilderSlice<unknown>>;
/** The store-wide `set`/`get` a slice is handed — also what the job runners bind to. */
export type CellBuilderSet = SliceArgs[0];
export type CellBuilderGet = SliceArgs[1];
