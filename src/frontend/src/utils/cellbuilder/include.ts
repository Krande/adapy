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
 * The same structure reading (`itemStructure`) says where a component's
 * coordinates are measured from (`itemOrigin`: its structure's origin).
 * Pure functions over the builder's cells and groups -- no store, no scene.
 */

import type {BuilderCell} from "@/state/cellbuilder/types";
import {containingCell, hasStructureOrigins, isGroupIncluded, structureNameToGroup, structureOrigin, type CellGroup} from "./groups";

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
 * exists IN ITS OWN STRUCTURE (a named seat is scoped the same way `containingCell`'s
 * geometry fallback already is -- a structure's origin must not let another
 * structure's cell with the same name, or at the same local coordinates, resolve
 * instead), else the space under it (the cell it is in, or stands on; among its
 * own structure's when it names one, see `containingCell`). Null for a space
 * itself, a loft band, and a component in no space. */
export function itemCell(item: BuilderCell, cells: Record<string, BuilderCell>): BuilderCell | null {
  if (item.kind === "cell" || item.kind === "loft") return null;
  const named = item.params?.SPACE_NAME;
  if (typeof named === "string" && named) {
    const structure = structureNameToGroup(item.params ?? {});
    const hit = Object.values(cells).find(
      (c) => c.kind === "cell" && c.name === named && (structure === undefined || c.group === structure),
    );
    if (hit) return hit;
  }
  return containingCell(item, Object.values(cells)) ?? null;
}

/** Where a component's coordinates are measured from: its structure's origin
 * (`structureOrigin`), [0, 0, 0] for one in no structure. Its stored box plus
 * this is where it is in the model. */
export function itemOrigin(
  item: BuilderCell,
  cells: Record<string, BuilderCell>,
  groups: readonly CellGroup[],
  loftMembers: readonly {NAME: string; STRUCTURE_NAME?: string}[] = [],
): readonly [number, number, number] {
  if (!hasStructureOrigins(groups)) return [0, 0, 0];
  return structureOrigin(groups, itemStructure(item, cells, groups, loftMembers));
}

/** The components with their boxes moved to where they are in the model (each
 * stored box plus its structure's origin), by id: what model-wide measures read
 * (extents, framing, recentring). The same map when no structure has an origin. */
export function placedCells(
  cells: Record<string, BuilderCell>,
  groups: readonly CellGroup[],
  loftMembers: readonly {NAME: string; STRUCTURE_NAME?: string}[] = [],
): Record<string, BuilderCell> {
  if (!hasStructureOrigins(groups)) return cells;
  const out: Record<string, BuilderCell> = {};
  for (const [id, c] of Object.entries(cells)) {
    const o = itemOrigin(c, cells, groups, loftMembers);
    out[id] = {...c, origin: [c.origin[0] + o[0], c.origin[1] + o[1], c.origin[2] + o[2]]};
  }
  return out;
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

/** How the builder draws a component in include view `view` (state/includeViewStore): hidden, or
 * drawn, faint with dashed edges when it is left out of the build and the view dims those. */
export function includeLook(
  state: IncludeState,
  view: "dimmed" | "included" | "all",
): {visible: boolean; dimmed: boolean} {
  if (state === "included" || view === "all") return {visible: true, dimmed: false};
  return view === "included" ? {visible: false, dimmed: false} : {visible: true, dimmed: true};
}
