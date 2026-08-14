/**
 * Pure helpers for cellbuilder cell GROUPS — a group is one structure the
 * (grouping-capable) engine compiles with its own blueprint. Kept side-effect
 * free and store-independent so the group round-trip is node-testable, mirroring
 * `blueprints.ts`/`history.ts`.
 *
 * Wire shape (see `ProceduralDoc`): each space doc carries `STRUCTURE_NAME` =
 * its group name (omitted when ungrouped); the doc carries a top-level
 * `groups: [{name, blueprint}]`. In the store a cell holds its group on a
 * first-class `group?: string` field.
 */

export interface CellGroup {
  name: string;
  blueprint: string;
}

/** The `STRUCTURE_NAME` to serialize on a space doc for a cell assigned to
 * `group`. Ungrouped cells (null/empty/blank) get `undefined` so the caller can
 * OMIT the key entirely (backward-compatible with ungrouped docs). */
export function groupToStructureName(group: string | null | undefined): string | undefined {
  const g = (group ?? "").trim();
  return g.length ? g : undefined;
}

/** Read a cell's group back from a serialized space doc's `STRUCTURE_NAME`
 * (inverse of {@link groupToStructureName}). A blank/missing value is ungrouped
 * (`undefined`). */
export function structureNameToGroup(space: Record<string, unknown>): string | undefined {
  const raw = space?.["STRUCTURE_NAME"];
  return typeof raw === "string" && raw.trim().length ? raw : undefined;
}

/** Normalize a groups list for serialization: trim names, drop blank/duplicate
 * names (first wins), preserve authored order. */
export function normalizeGroups(groups: readonly CellGroup[]): CellGroup[] {
  const seen = new Set<string>();
  const out: CellGroup[] = [];
  for (const g of groups ?? []) {
    const name = (g?.name ?? "").trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push({ name, blueprint: g.blueprint });
  }
  return out;
}

/** A cell's group after some groups are deleted: cleared (`undefined`) when it
 * pointed at a removed group, otherwise unchanged. Drives `removeGroup`'s cell
 * unassignment. */
export function groupAfterRemoval(
  group: string | undefined,
  removed: ReadonlySet<string>,
): string | undefined {
  return group && removed.has(group) ? undefined : group;
}

/** Reconcile a cell's group against the currently-defined groups: a group that
 * no longer exists (renamed/deleted out from under the cell) becomes ungrouped.
 * Keeps the doc self-consistent after a load. */
export function resolveCellGroup(
  group: string | null | undefined,
  groups: readonly CellGroup[],
): string | undefined {
  const g = (group ?? "").trim();
  if (!g.length) return undefined;
  return groups.some((x) => x.name === g) ? g : undefined;
}
