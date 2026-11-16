/**
 * Whether a procedural component is INCLUDED in the build -- the ONE reading of
 * the document's INCLUDE flags, for the Model panel's checkboxes and for any view
 * that draws excluded components differently (dimmed, dashed, hidden).
 *
 * The flags live in the document, nowhere else:
 *   - a space / equipment / opening carries its own `INCLUDE` (the workbook's
 *     INCLUDE column), kept verbatim in `BuilderCell.params`; absent = included,
 *     only an explicit `false` excludes (mirrors `isGroupIncluded`);
 *   - a structure (group) carries `include: false` when left out (the Structures
 *     sheet), see `utils/cellbuilder/groups.ts`;
 *   - a loft member's `INCLUDE: false` produces no band cells at all
 *     (`memberToBands`), so a loft band that exists is always included.
 *
 * A component is BUILT only when it is included itself AND its structure is.
 * Pure functions over the builder's cells and groups -- no store, no scene.
 */

import type {BuilderCell} from "@/state/cellbuilder/types";
import {containingCell, isGroupIncluded, type CellGroup} from "./groups";

/** The INCLUDE key as the workbook and the doc spell it. */
export const INCLUDE_KEY = "INCLUDE";

/** The component's own INCLUDE flag: only an explicit `false` excludes. */
export function isItemIncluded(item: Pick<BuilderCell, "kind" | "params">): boolean {
  if (item.kind === "loft") return true;
  return item.params?.[INCLUDE_KEY] !== false;
}

/** `params` with the INCLUDE flag set: `false` is written, `true` leaves the
 * key out (the doc writer emits `INCLUDE: true` by default, so the two read the
 * same and a never-excluded doc stays as it was). */
export function withInclude(params: Record<string, unknown>, include: boolean): Record<string, unknown> {
  const next = {...params};
  if (include) delete next[INCLUDE_KEY];
  else next[INCLUDE_KEY] = false;
  return next;
}

/** The structure (group) a component belongs to, by what the document says
 * first and by geometry last:
 *   - a space: its own group (`STRUCTURE_NAME`);
 *   - equipment / opening: its `STRUCTURE_NAME` when that names a group, else the
 *     group of the space its `SPACE_NAME` names, else the space it sits in;
 *   - a loft band: its member's `STRUCTURE_NAME`.
 * `undefined` for a component in no structure. */
export function itemStructure(
  item: BuilderCell,
  cells: Record<string, BuilderCell>,
  groups: readonly CellGroup[],
  loftMembers: readonly {NAME: string; STRUCTURE_NAME?: string}[] = [],
): string | undefined {
  if (item.kind === "cell") return item.group;
  if (item.kind === "loft") {
    const member = loftMembers.find((m) => m.NAME === item.loft?.member);
    const name = member?.STRUCTURE_NAME;
    return typeof name === "string" && groups.some((g) => g.name === name) ? name : undefined;
  }
  const own = item.params?.STRUCTURE_NAME;
  if (typeof own === "string" && own.trim() && groups.some((g) => g.name === own)) return own;
  return itemCell(item, cells)?.group;
}

/** The space a component is placed in: the one its `SPACE_NAME` names when it
 * exists, else the space under it (the cell it is in, or stands on). Null for a
 * space itself, a loft band, and a component in no space. */
export function itemCell(item: BuilderCell, cells: Record<string, BuilderCell>): BuilderCell | null {
  if (item.kind === "cell" || item.kind === "loft") return null;
  const named = item.params?.SPACE_NAME;
  if (typeof named === "string" && named) {
    const hit = Object.values(cells).find((c) => c.kind === "cell" && c.name === named);
    if (hit) return hit;
  }
  return containingCell(item, Object.values(cells)) ?? null;
}

/** How a component stands in the build. */
export type IncludeState =
  /** Built: included itself, in an included structure (or none). */
  | "included"
  /** Its own INCLUDE is false. */
  | "excluded"
  /** Included itself, but its structure is left out of the build. */
  | "excluded-by-structure";

/** The component's effective include state -- what a view that dims or hides
 * excluded components should read, and what the Model panel's rows show. */
export function itemIncludeState(
  item: BuilderCell,
  cells: Record<string, BuilderCell>,
  groups: readonly CellGroup[],
  loftMembers: readonly {NAME: string; STRUCTURE_NAME?: string}[] = [],
): IncludeState {
  if (!isItemIncluded(item)) return "excluded";
  const structure = itemStructure(item, cells, groups, loftMembers);
  if (structure && !isGroupIncluded(groups.find((g) => g.name === structure))) return "excluded-by-structure";
  return "included";
}

/** Is the component built by the next compile? */
export function isItemBuilt(
  item: BuilderCell,
  cells: Record<string, BuilderCell>,
  groups: readonly CellGroup[],
  loftMembers: readonly {NAME: string; STRUCTURE_NAME?: string}[] = [],
): boolean {
  return itemIncludeState(item, cells, groups, loftMembers) === "included";
}
