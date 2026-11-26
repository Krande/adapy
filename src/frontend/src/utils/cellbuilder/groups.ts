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
 *
 * `origin: [x, y, z]` is where the structure sits in the model (the engine's
 * structure position). Everything in a structure -- its spaces, equipment,
 * openings, loft bands, a plugin's refs -- is stored RELATIVE to it, and the
 * builder draws and edits it in a frame moved by it. Written whenever the group
 * has one, [0, 0, 0] too: an engine that saves the position back must tell "moved
 * to zero" from "a viewer that knows no origins". A group without one (a new
 * group, an older doc) writes none and sits at [0, 0, 0].
 */

export type StructureOrigin = [number, number, number];

export interface CellGroup {
  name: string;
  blueprint: string;
  /** False = left out of the build. Absent (or true) = built. */
  include?: boolean;
  /** Where the structure sits; its items are stored relative to it. Absent = [0, 0, 0]. */
  origin?: StructureOrigin;
  /** Everything else a doc's group entry carries that this viewer does not normalise: a
   *  grouping engine's own per-group settings (python's `TopoStructure` extras --
   *  MODEL_INPUT, BLUEPRINT, CONDITION_NAME, ...). We must not eat another tool's columns just
   *  because the viewer has no use for them itself: `groupToWire`/`groupFromWire` round-trip
   *  them untouched. */
  [extra: string]: unknown;
}

const ZERO_ORIGIN: readonly [number, number, number] = [0, 0, 0];

/** A raw `origin` as three finite numbers, or undefined when it is not one. */
function originFromWire(raw: unknown): StructureOrigin | undefined {
  if (!Array.isArray(raw) || raw.length !== 3) return undefined;
  const v = raw.map((x) => (typeof x === "number" || typeof x === "string" ? Number(x) : NaN));
  return v.every(Number.isFinite) ? [v[0], v[1], v[2]] : undefined;
}

/** Where structure `name` sits: its group's origin; [0, 0, 0] for no structure,
 * "*" (every structure), a name that is not a group, or a group without one. */
export function structureOrigin(
  groups: readonly CellGroup[],
  name: string | null | undefined,
): readonly [number, number, number] {
  if (!name || name === "*") return ZERO_ORIGIN;
  return groups.find((g) => g.name === name)?.origin ?? ZERO_ORIGIN;
}

/** Does any structure sit away from [0, 0, 0]? False lets a caller skip the per-item lookups. */
export function hasStructureOrigins(groups: readonly CellGroup[]): boolean {
  return groups.some((g) => !!g.origin && (g.origin[0] !== 0 || g.origin[1] !== 0 || g.origin[2] !== 0));
}

/** Is this group built by the next compile? Only an explicit `false` excludes. */
export function isGroupIncluded(group: Pick<CellGroup, "include"> | null | undefined): boolean {
  return group?.include !== false;
}

/** The keys this viewer normalises itself; anything else on a group is someone else's and goes
 *  back as it came (see {@link CellGroup}'s `extra` catch-all). */
const GROUP_OWN_KEYS = new Set(["name", "blueprint", "include", "origin"]);

/** A group as written to the doc: `include` only when it is false, `origin`
 * whenever the group has one, and any extra (non-normalised) key spread back in untouched --
 * so a round trip through `groupFromWire` -> edit -> `groupToWire` keeps whatever another tool
 * put on this group alongside the keys the viewer itself writes. */
export function groupToWire(group: CellGroup): CellGroup {
  const origin = originFromWire(group.origin);
  const extra: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(group)) if (!GROUP_OWN_KEYS.has(k) && v !== undefined) extra[k] = v;
  return {
    ...extra,
    name: group.name,
    blueprint: group.blueprint,
    ...(isGroupIncluded(group) ? {} : { include: false }),
    ...(origin ? { origin } : {}),
  };
}

/** A doc's raw group entry read back (inverse of {@link groupToWire}). Anything
 * other than an explicit `include: false` reads as built; an `origin` that is not
 * three numbers reads as none. Every key besides `name`/`blueprint`/`include`/`origin` is kept
 * verbatim (another tool's own per-group settings), through `groupToWire`'s own extra-key spread. */
export function groupFromWire(raw: { name?: unknown; blueprint?: unknown; include?: unknown; origin?: unknown; [extra: string]: unknown }): CellGroup {
  return groupToWire({
    ...raw,
    name: String(raw?.name ?? ""),
    blueprint: String(raw?.blueprint ?? ""),
    include: raw?.include !== false,
    origin: originFromWire(raw?.origin),
  } as CellGroup);
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
 * is false and `origin` when there is one ({@link groupToWire}). */
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
  return containingCell(obj, cells)?.group;
}

/** The space a builder object sits in, by geometry: the cell under it in plan
 * whose floor is highest below its centre -- the cell it is in, or, for a unit
 * seated on a roof, the cell it stands on. Undefined for a space itself and for
 * an object over no cell. An object that names its structure (`STRUCTURE_NAME`)
 * looks only among that structure's cells: coordinates are relative to a
 * structure's origin, so another structure's cell at the same numbers is not
 * where it is. */
export function containingCell<C extends { kind: string; group?: string; origin: readonly number[]; size: readonly number[] }>(
  obj: { kind: string; origin: readonly number[]; size: readonly number[]; params?: Record<string, unknown> },
  cells: Iterable<C>,
): C | undefined {
  if (obj.kind === "cell") return undefined;
  const structure = obj.params ? structureNameToGroup(obj.params) : undefined;
  const cx = obj.origin[0] + obj.size[0] / 2;
  const cy = obj.origin[1] + obj.size[1] / 2;
  const cz = obj.origin[2] + obj.size[2] / 2;
  let best: C | undefined;
  for (const c of cells) {
    if (c.kind !== "cell") continue;
    if (structure !== undefined && c.group !== structure) continue;
    const inPlan =
      cx >= c.origin[0] && cx <= c.origin[0] + c.size[0] &&
      cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
    if (!inPlan || c.origin[2] > cz + 1e-6) continue;
    if (!best || c.origin[2] > best.origin[2]) best = c;
  }
  return best;
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

/** The structure a new space goes into: `activeGroup` while it still names a group, else none. */
export function activeStructure(s: {activeGroup: string | null; groups: readonly CellGroup[]}): string | undefined {
  const name = s.activeGroup;
  return name !== null && s.groups.some((g) => g.name === name) ? name : undefined;
}
