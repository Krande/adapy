/**
 * Pure helpers for cellbuilder cell GROUPS — a group is one structure the
 * (grouping-capable) engine compiles with its own blueprint. Kept side-effect
 * free and store-independent so the group round-trip is node-testable, mirroring
 * `blueprints.ts`/`history.ts`.
 *
 * Wire shape (see `ProceduralDoc`): each space doc carries `STRUCTURE_NAME` =
 * its group name (omitted when ungrouped); the doc carries a top-level
 * `groups: [{name, blueprint, include?}]`. In the store a cell holds its group on
 * a first-class `group?: string` field.
 *
 * `include: false` leaves the group's structure out of the next compile. It is
 * written ONLY when false: a built group carries no `include` key, so a doc that
 * never excluded anything stays byte-identical to one written before the flag
 * existed, and an engine that predates it builds every group as before.
 */

export interface CellGroup {
  name: string;
  blueprint: string;
  /** False = left out of the build. Absent (or true) = built. */
  include?: boolean;
}

/** Is this group built by the next compile? Only an explicit `false` excludes. */
export function isGroupIncluded(group: Pick<CellGroup, "include"> | null | undefined): boolean {
  return group?.include !== false;
}

/** A group as written to the doc: `include` only when it is false. */
export function groupToWire(group: CellGroup): CellGroup {
  return isGroupIncluded(group)
    ? { name: group.name, blueprint: group.blueprint }
    : { name: group.name, blueprint: group.blueprint, include: false };
}

/** A doc's raw group entry read back (inverse of {@link groupToWire}). Anything
 * other than an explicit `include: false` reads as built. */
export function groupFromWire(raw: { name?: unknown; blueprint?: unknown; include?: unknown }): CellGroup {
  const name = String(raw?.name ?? "");
  const blueprint = String(raw?.blueprint ?? "");
  return raw?.include === false ? { name, blueprint, include: false } : { name, blueprint };
}

/** Default group colours: distinct, readable on the dark viewer, and stable for a
 * group as long as its position in the list is (the doc keeps authored order). */
export const GROUP_PALETTE: readonly string[] = [
  "#e6194b",
  "#3cb44b",
  "#4363d8",
  "#f58231",
  "#911eb4",
  "#42d4f4",
  "#f032e6",
  "#bfef45",
  "#fabed4",
  "#469990",
];

/** The colour a group draws with: the viewer's override for it, else the palette
 * entry for its position. Null for a name that is not a group. */
export function groupColor(
  groups: readonly CellGroup[],
  name: string | null | undefined,
  overrides: Readonly<Record<string, string>> = {},
): string | null {
  if (!name) return null;
  const i = groups.findIndex((g) => g.name === name);
  if (i < 0) return null;
  return overrides[name] ?? GROUP_PALETTE[i % GROUP_PALETTE.length];
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
 * names (first wins), preserve authored order, and write `include` only when it
 * is false ({@link groupToWire}). */
export function normalizeGroups(groups: readonly CellGroup[]): CellGroup[] {
  const seen = new Set<string>();
  const out: CellGroup[] = [];
  for (const g of groups ?? []) {
    const name = (g?.name ?? "").trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(groupToWire({ ...g, name }));
  }
  return out;
}

/** The group a builder object belongs to: a cell's own group; equipment and
 * openings (which carry none) take the group of the cell they sit in, so hiding
 * or isolating a group takes its contents with it. */
export function objectGroup(
  obj: { kind: string; group?: string; origin: readonly number[]; size: readonly number[] },
  cells: Iterable<{ kind: string; group?: string; origin: readonly number[]; size: readonly number[] }>,
): string | undefined {
  if (obj.kind === "cell") return obj.group;
  const cx = obj.origin[0] + obj.size[0] / 2;
  const cy = obj.origin[1] + obj.size[1] / 2;
  const cz = obj.origin[2] + obj.size[2] / 2;
  // The cell under it in plan whose floor is highest below its centre: the cell it
  // is in, or -- for a unit seated on a roof -- the cell it stands on.
  let best: { group?: string; z: number } | null = null;
  for (const c of cells) {
    if (c.kind !== "cell") continue;
    const inPlan =
      cx >= c.origin[0] && cx <= c.origin[0] + c.size[0] &&
      cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
    if (!inPlan || c.origin[2] > cz + 1e-6) continue;
    if (!best || c.origin[2] > best.z) best = { group: c.group, z: c.origin[2] };
  }
  return best?.group;
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
