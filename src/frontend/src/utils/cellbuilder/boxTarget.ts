// What a builder pick stands for: a cell, or a plugin's ref (state/cellbuilder/refsSlice.ts).
// Only the paths that treat a pick as a box take both -- the move gizmo, the resize handles,
// the face drag, the name a panel shows. Everything else keeps looking up cells by `cellId`,
// and a ref, which names no cell, passes it by.

import type {BuilderCell, BuilderSelection} from "@/state/cellbuilder/types";
import type {BuilderRef} from "@/state/cellbuilder/refsSlice";
import type {CellGroup} from "./groups";
import {itemCell, itemStructure} from "./include";

export type BoxTarget = {kind: "cell"; id: string; cell: BuilderCell} | {kind: "ref"; id: string; ref: BuilderRef};

/** The cell or the ref `sel` picks; null when nothing is picked or it is gone. */
export function selectionTarget(
  sel: BuilderSelection | null,
  cells: Readonly<Record<string, BuilderCell>>,
  refs: Readonly<Record<string, BuilderRef>>,
): BoxTarget | null {
  if (!sel) return null;
  if (sel.kind === "ref") {
    const ref = refs[sel.refId];
    return ref ? {kind: "ref", id: ref.id, ref} : null;
  }
  const cell = cells[sel.cellId];
  return cell ? {kind: "cell", id: cell.id, cell} : null;
}

/** The ref `sel` picks, or null. */
export function selectedRef(
  sel: BuilderSelection | null,
  refs: Readonly<Record<string, BuilderRef>>,
): BuilderRef | null {
  return sel?.kind === "ref" ? (refs[sel.refId] ?? null) : null;
}

/** The picked cell's or ref's name; null when nothing is picked or it is gone. */
export function selectionName(
  sel: BuilderSelection | null,
  cells: Readonly<Record<string, BuilderCell>>,
  refs: Readonly<Record<string, BuilderRef>>,
): string | null {
  const t = selectionTarget(sel, cells, refs);
  return t ? (t.kind === "cell" ? t.cell.name : t.ref.name) : null;
}

/** The structure and the space a pick belongs to, by name: a space is its own space; a
 *  component is in the space it is placed in; a ref is where it says, its structure that of
 *  its space when it names none. Either is undefined when the pick is in none. */
export function selectionPlace(
  sel: BuilderSelection | null,
  s: {
    cells: Record<string, BuilderCell>;
    refs: Readonly<Record<string, BuilderRef>>;
    groups: readonly CellGroup[];
    loftMembers?: readonly {NAME: string; STRUCTURE_NAME?: string}[];
  },
): {structure?: string; space?: string} {
  const t = selectionTarget(sel, s.cells, s.refs);
  if (!t) return {};
  if (t.kind === "ref") {
    const space = t.ref.space;
    const spaceCell = space ? Object.values(s.cells).find((c) => c.kind === "cell" && c.name === space) : undefined;
    return {structure: t.ref.structure ?? spaceCell?.group, space};
  }
  const c = t.cell;
  let structure: string | undefined;
  try {
    structure = itemStructure(c, s.cells, s.groups, s.loftMembers ?? []);
  } catch {
    // A half-made cell (no box yet) is in no structure.
  }
  const space = c.kind === "cell" ? c.name : (itemCell(c, s.cells)?.name ?? undefined);
  return {structure, space};
}
