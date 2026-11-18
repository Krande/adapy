/**
 * The STRUCTURE the Builder's selection is about -- what the Compile settings'
 * Blueprint row follows, so selecting a structure (or anything in it) in the
 * Model panel shows that structure's blueprint, not the model-level one.
 *
 * Every selected component (the primary selection and the multi-select set)
 * must belong to one structure (utils/cellbuilder/include `itemStructure`):
 * then that structure is in focus. No selection, a selection across structures,
 * or one in no structure: nothing is, and the model-level blueprint shows.
 *
 * Pure over the builder's state; no store.
 */

import type {BuilderCell, BuilderSelection} from "@/state/cellbuilder/types";
import type {CellGroup} from "./groups";
import {itemStructure} from "./include";

export function focusedStructure(
  selection: BuilderSelection | null,
  selectedCellIds: readonly string[],
  cells: Record<string, BuilderCell>,
  groups: readonly CellGroup[],
  loftMembers: readonly {NAME: string; STRUCTURE_NAME?: string}[] = [],
): string | undefined {
  const ids = new Set(selectedCellIds);
  if (selection) ids.add(selection.cellId);
  let found: string | undefined;
  for (const id of ids) {
    const cell = cells[id];
    if (!cell) continue;
    const s = itemStructure(cell, cells, groups, loftMembers);
    if (!s) return undefined;
    if (found === undefined) found = s;
    else if (found !== s) return undefined;
  }
  return found;
}
