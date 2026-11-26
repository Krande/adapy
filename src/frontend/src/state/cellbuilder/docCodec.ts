/**
 * Stored document <-> builder model CODEC.
 *
 * Owns: reading a `ProceduralDoc` into the builder's cells / loft members /
 * systems / groups (and the equipment placement offset that reconciles
 * space-local coordinates), plus the small lookups the writer needs. Pure
 * functions over documents — no store access, no `set`/`get` — so both the
 * editing store and a read-only companion model can share one conversion.
 */

import type {ProceduralDoc} from "@/services/viewerApi";
import {
  groupFromWire,
  normalizeGroups,
  resolveCellGroup,
  structureNameToGroup,
  type CellGroup,
} from "@/utils/cellbuilder/groups";
import {placedCells} from "@/utils/cellbuilder/include";
import {bandBounds, memberToBands, type LoftMemberDoc} from "@/utils/cellbuilder/loft";
import type {BuilderCell, BuilderSystem, SystemType} from "./types";
import {nextId} from "./shared";

// Geometry/system keys consumed by the builder itself; everything else an
// entity dump carries lands in BuilderCell.params and round-trips verbatim.
const SPACE_OWN_KEYS = new Set(["NAME", "X", "Y", "Z", "DX", "DY", "DZ", "STRUCTURE_NAME"]);
const EQUIPMENT_OWN_KEYS = new Set([
  "NAME",
  "DESCRIPTION",
  "X",
  "Y",
  "Z",
  "LX",
  "LY",
  "LZ",
  "GLOBAL_COORDS",
  "ROT_X",
  "ROT_Y",
  "ROT_Z",
]);
const OPENING_OWN_KEYS = new Set([
  "NAME",
  "SUBTYPE",
  "USE_GLOBAL_COORDS",
  "X",
  "Y",
  "Z",
  "DX",
  "DY",
  "DZ",
]);

/** The doc's top-level keys the builder writes (toDoc), each whenever it has one: everything else
 * in a loaded doc is someone else's -- a run tool's own lists, `engine` -- and goes back as it
 * came (`docExtras`). Writing only these dropped the rest on every commit.
 *
 * `structures` is listed here even though `toDoc` never writes it: a legacy doc's `structures`
 * list is read once, into `groups` (`groupsFromDoc`'s fallback), and from then on this is a
 * builder-owned key like any other -- a committed doc carries `groups` only, never both shapes. */
export const BUILDER_DOC_KEYS: ReadonlySet<string> = new Set([
  "schema_version",
  "grid",
  "blueprint",
  "blueprint_name",
  "design_rules",
  "detailing",
  "equipment_cad",
  "spaces",
  "equipments",
  "systems",
  "openings",
  "loft_members",
  "groups",
  "structures",
]);

/** The loaded doc's keys the builder does not write, to write back untouched. */
export function docExtras(doc: object | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(doc ?? {})) if (!BUILDER_DOC_KEYS.has(k) && v !== undefined) out[k] = v;
  return out;
}

/** The sides of a space an opening can be placed on (TopoOpening.SPACE_SIDE). */
export const OPENING_SIDES = ["X", "-X", "Y", "-Y", "Z", "-Z"] as const;
export type OpeningSide = (typeof OPENING_SIDES)[number];

type Vec3 = [number, number, number];

/** A side's axes: `n` through the side, `u` / `v` the plane POS_X / POS_Y run in (ada's
 * TopoOpening._calculate_p1_p2_from_local), and whether the side is at the far end of `n`. */
function sideAxes(side: OpeningSide): {n: 0 | 1 | 2; u: 0 | 1 | 2; v: 0 | 1 | 2; far: boolean} {
  const far = !side.startsWith("-");
  if (side.endsWith("Z")) return {n: 2, u: 0, v: 1, far};
  if (side.endsWith("Y")) return {n: 1, u: 0, v: 2, far};
  return {n: 0, u: 1, v: 2, far};
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)) ? Number(v) : null);

/** The box of an opening placed on a side of `space` (its POS / SIZE / DEPTH), as ada builds it; null
 * when a column is missing or the side is not one of the six. */
export function localOpeningBox(o: Record<string, unknown>, space: {origin: Vec3; size: Vec3}): {origin: Vec3; size: Vec3} | null {
  const side = o.SPACE_SIDE as OpeningSide;
  if (!OPENING_SIDES.includes(side)) return null;
  const [px, py, sx, sy, depth] = [num(o.POS_X), num(o.POS_Y), num(o.SIZE_X), num(o.SIZE_Y), num(o.DEPTH)];
  if (px === null || py === null || sx === null || sy === null || depth === null) return null;
  const {n, u, v, far} = sideAxes(side);
  const origin: Vec3 = [0, 0, 0];
  const size: Vec3 = [0, 0, 0];
  origin[u] = space.origin[u] + px;
  size[u] = sx;
  origin[v] = space.origin[v] + py;
  size[v] = sy;
  origin[n] = space.origin[n] + (far ? space.size[n] : 0) - depth / 2;
  size[n] = depth;
  return {origin, size};
}

/** The local columns that place `box` on `side` of `space` (the inverse of localOpeningBox). The box's
 * position through the side is not kept: the side is the plane it sits in. */
export function localOpeningColumns(box: {origin: Vec3; size: Vec3}, space: {origin: Vec3}, side: OpeningSide) {
  const {n, u, v} = sideAxes(side);
  // To the micrometre: the subtraction back from the space origin leaves float noise (2.5999999999999996)
  // that would otherwise land in the workbook.
  const um = (x: number) => Math.round(x * 1e6) / 1e6;
  return {
    POS_X: um(box.origin[u] - space.origin[u]),
    POS_Y: um(box.origin[v] - space.origin[v]),
    SIZE_X: um(box.size[u]),
    SIZE_Y: um(box.size[v]),
    DEPTH: um(box.size[n]),
  };
}

const spaceBox = (s: Record<string, unknown>): {origin: Vec3; size: Vec3} => ({
  origin: [Number(s.X ?? 0), Number(s.Y ?? 0), Number(s.Z ?? 0)],
  size: [Number(s.DX ?? 1), Number(s.DY ?? 1), Number(s.DZ ?? 1)],
});

/** The doc's openings the builder cannot draw (no global box, and no space side it can resolve): kept
 * as they came, so a commit writes them back instead of dropping them. */
export function unplacedOpeningsFromDoc(doc: ProceduralDoc): Record<string, unknown>[] {
  const spaces = new Map<string, Record<string, unknown>>();
  for (const s of doc.spaces ?? []) if (typeof s.NAME === "string") spaces.set(s.NAME, s);
  return ((doc as {openings?: Record<string, unknown>[]}).openings ?? []).filter((o) => {
    if (o.X != null && o.DX != null) return false;
    const space = typeof o.SPACE_NAME === "string" ? spaces.get(o.SPACE_NAME) : undefined;
    return !space || !localOpeningBox(o, spaceBox(space));
  });
}

/** The doc schema the builder writes (ada.topo_model.engines.PROCEDURAL_SCHEMA_VERSION). */
export const DOC_SCHEMA_VERSION = "1.1";

/** Does a doc of this schema mean its entities' `INCLUDE: false`? From 1.1 only: before it every stored
 * doc carried the entity models' default false on items nobody excluded, and no build read it
 * (ada.topo_model.engines.honours_entity_include). */
export function honoursEntityInclude(schemaVersion: unknown): boolean {
  const have = String(schemaVersion ?? "").split(".").map(Number);
  if (!have.length || have.some((n) => !Number.isInteger(n))) return false;
  const [maj = 0, min = 0] = have;
  return maj > 1 || (maj === 1 && min >= 1);
}

function extractParamsRaw(
  entity: Record<string, unknown>,
  ownKeys: Set<string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(entity)) {
    if (ownKeys.has(k) || v === null || v === undefined) continue;
    out[k] = v;
  }
  return out;
}

// Global offset that seats a cell-associated equipment at its cell. Equipment
// X/Y/Z are LOCAL to their SPACE_NAME cell — the default — unless GLOBAL_COORDS
// is set, matching the compile worker (equipment_space_offset) and the
// simulation view. A ROOF-seated unit also picks up the cell height. Global or
// unresolved-cell equipment get no offset (X/Y/Z are already world coords). This
// is why an imported model's equipment used to render at the wrong spot: their
// local coords were placed as if global.
function equipmentSpaceOffset(
  e: Record<string, unknown>,
  spaceByName: Map<string, Record<string, unknown>>,
): [number, number, number] {
  if (e.GLOBAL_COORDS) return [0, 0, 0];
  const s = spaceByName.get(e.SPACE_NAME as string);
  if (!s) return [0, 0, 0];
  const oz =
    Number(s.Z ?? 0) + (e.SPACE_LOC === "ROOF" ? Number(s.DZ ?? 0) : 0);
  return [Number(s.X ?? 0), Number(s.Y ?? 0), oz];
}

/** The doc's cell groups (name + per-group blueprint + `include: false` when
 * left out of the build + `origin` where it has one), normalized (blank/dup
 * names dropped). Empty for an ungrouped doc.
 *
 * `groups` wins when present. A doc with none, but with a legacy top-level
 * `structures` list (`{NAME, X?, Y?, Z?, INCLUDE?}` -- an engine's own structure
 * records, predating `groups[]`), reads those into the SAME shape instead: each
 * becomes a group named after the structure, at its origin, built unless its
 * INCLUDE is explicitly false, with the model's own blueprint (no per-structure
 * blueprint exists on that legacy shape). This runs once, on load: `structures`
 * is a builder-owned key from here on (BUILDER_DOC_KEYS), so the next commit
 * writes `groups` only -- a doc never carries both shapes after that. */
export function groupsFromDoc(doc: ProceduralDoc): CellGroup[] {
  const own = (doc.groups ?? []).map(groupFromWire);
  if (own.length) return normalizeGroups(own);
  const legacy = (doc as unknown as Record<string, unknown>).structures;
  if (!Array.isArray(legacy) || !legacy.length) return [];
  const modelBlueprint = typeof doc.blueprint_name === "string" ? doc.blueprint_name : "";
  return normalizeGroups(
    legacy.map((raw: Record<string, unknown>) => {
      const x = Number(raw?.X ?? 0);
      const y = Number(raw?.Y ?? 0);
      const z = Number(raw?.Z ?? 0);
      const origin: [number, number, number] | undefined = x || y || z ? [x, y, z] : undefined;
      // A legacy structure can carry its own extra columns too (another tool's per-structure
      // settings, e.g. an engine's own MODEL_INPUT/CONDITION_NAME), just as a `groups[]` entry
      // can -- copy them across the same way, as python does when it folds structures into
      // groups, rather than dropping them the moment this legacy shape becomes `groups[]`.
      const extra: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(raw ?? {})) {
        if (k !== "NAME" && k !== "X" && k !== "Y" && k !== "Z" && k !== "INCLUDE" && v !== undefined) extra[k] = v;
      }
      return groupFromWire({
        ...extra,
        name: raw?.NAME,
        blueprint: modelBlueprint,
        include: raw?.INCLUDE !== false,
        origin,
      });
    }),
  );
}

/** Convert a stored document into builder cells.
 *
 * Exported so a companion model can be drawn read-only from its doc without
 * going through the editing store — the conversion is the same, and a second
 * copy of it would be a second thing to keep in step with the schema. */
export function cellsFromDoc(doc: ProceduralDoc): Record<string, BuilderCell> {
  const out: Record<string, BuilderCell> = {};
  const spaceByName = new Map<string, Record<string, unknown>>();
  for (const s of doc.spaces ?? []) {
    const nm = s.NAME as string | undefined;
    if (nm) spaceByName.set(nm, s);
  }
  // Reconcile each cell's group against the doc's group list: a STRUCTURE_NAME
  // naming no defined group is dropped to ungrouped (keeps the model consistent).
  const groups = groupsFromDoc(doc);
  // An older doc's entity INCLUDE is the models' default, not a choice: left out, it reads as included
  // and the next save writes the 1.1 stamp without it.
  const own = honoursEntityInclude(doc.schema_version);
  const extractParams = (entity: Record<string, unknown>, ownKeys: Set<string>) => {
    const p = extractParamsRaw(entity, ownKeys);
    if (!own) delete p.INCLUDE;
    return p;
  };
  for (const s of doc.spaces ?? []) {
    const id = nextId();
    out[id] = {
      id,
      name: String(s.NAME ?? id),
      kind: "cell",
      origin: [Number(s.X ?? 0), Number(s.Y ?? 0), Number(s.Z ?? 0)],
      size: [Number(s.DX ?? 1), Number(s.DY ?? 1), Number(s.DZ ?? 1)],
      group: resolveCellGroup(structureNameToGroup(s), groups),
      params: extractParams(s, SPACE_OWN_KEYS),
    };
  }
  for (const e of doc.equipments ?? []) {
    const id = nextId();
    const [ox, oy, oz] = equipmentSpaceOffset(e, spaceByName);
    out[id] = {
      id,
      name: String(e.NAME ?? id),
      kind: "equipment",
      equipmentType:
        typeof e.DESCRIPTION === "string" && e.DESCRIPTION
          ? e.DESCRIPTION
          : undefined,
      origin: [
        Number(e.X ?? 0) + ox,
        Number(e.Y ?? 0) + oy,
        Number(e.Z ?? 0) + oz,
      ],
      size: [Number(e.LX ?? 1), Number(e.LY ?? 1), Number(e.LZ ?? 1)],
      rotation: [
        Number(e.ROT_X ?? 0),
        Number(e.ROT_Y ?? 0),
        Number(e.ROT_Z ?? 0),
      ],
      params: extractParams(e, EQUIPMENT_OWN_KEYS),
    };
  }
  // Openings: the builder's own are global boxes (X/Y/Z/DX/DY/DZ); a workbook's are placed on a
  // side of their space (SPACE_NAME, SPACE_SIDE, POS_X/POS_Y, SIZE_X/SIZE_Y, DEPTH). Both are drawn;
  // a local one keeps its local form (`openingFrame`). One the builder can place neither way is kept
  // aside as it came (unplacedOpeningsFromDoc) and written back unchanged.
  for (const o of (doc as { openings?: Record<string, unknown>[] }).openings ??
    []) {
    const global = o.X != null && o.DX != null;
    const space = typeof o.SPACE_NAME === "string" ? spaceByName.get(o.SPACE_NAME) : undefined;
    const local = !global && space ? localOpeningBox(o, spaceBox(space)) : null;
    if (!global && !local) continue;
    const id = nextId();
    out[id] = {
      id,
      name: String(o.NAME ?? id),
      kind: "opening",
      subtype:
        o.SUBTYPE === "window" ? "window" : o.SUBTYPE === "opening" ? "opening" : "door",
      origin: local ? local.origin : [Number(o.X ?? 0), Number(o.Y ?? 0), Number(o.Z ?? 0)],
      size: local ? local.size : [Number(o.DX ?? 1), Number(o.DY ?? 1), Number(o.DZ ?? 1)],
      ...(local ? { openingFrame: "local" as const } : {}),
      params: extractParams(o, OPENING_OWN_KEYS),
    };
  }
  // Loft members (Phase 2b, read-only): each member -> N-1 swept-band cells,
  // drawn from their two profile rings. INCLUDE=false members are skipped
  // (memberToBands returns []). The raw loft_members are retained separately on
  // the store so toDoc re-emits them verbatim — this slice never edits loft
  // geometry. A loft-only model (no spaces) loads + displays.
  for (const m of loftMembersFromDoc(doc)) {
    for (const band of memberToBands(m)) {
      const id = nextId();
      const { origin, size } = bandBounds(band);
      out[id] = {
        id,
        name: band.cellName,
        kind: "loft",
        origin,
        size,
        loft: band,
        params: {},
      };
    }
  }
  return out;
}

/** A doc's cells where they are in the model: each structure's at its origin
 * (`placedCells`). What a read-only companion is drawn and measured from. */
export function placedCellsFromDoc(doc: ProceduralDoc): BuilderCell[] {
  return Object.values(placedCells(cellsFromDoc(doc), groupsFromDoc(doc), loftMembersFromDoc(doc)));
}

/** The raw authored loft members carried on a doc (empty when absent). */
export function loftMembersFromDoc(doc: ProceduralDoc): LoftMemberDoc[] {
  const raw = (doc as { loft_members?: unknown }).loft_members;
  return Array.isArray(raw) ? (raw as LoftMemberDoc[]) : [];
}


export function systemsFromDoc(doc: ProceduralDoc): Record<string, BuilderSystem> {
  const out: Record<string, BuilderSystem> = {};
  for (const s of doc.systems ?? []) {
    const id = nextId();
    const conns = Array.isArray(s.CONNECTIONS)
      ? (s.CONNECTIONS as Record<string, unknown>[])
      : [];
    out[id] = {
      id,
      name: String(s.NAME ?? id),
      type: (typeof s.TYPE === "string" ? s.TYPE : "piping") as SystemType,
      medium: typeof s.MEDIUM === "string" ? s.MEDIUM : undefined,
      connections: conns.map((c) =>
        c.SITE
          ? {
              site: String(c.SITE),
              position: Array.isArray(c.POSITION)
                ? ([
                    Number(c.POSITION[0]),
                    Number(c.POSITION[1]),
                    Number(c.POSITION[2]),
                  ] as [number, number, number])
                : ([0, 0, 0] as [number, number, number]),
              direction: c.DIRECTION === "OUT" ? "OUT" : "IN",
              directionVector: Array.isArray(c.DIRECTION_VECTOR)
                ? ([
                    Number(c.DIRECTION_VECTOR[0]),
                    Number(c.DIRECTION_VECTOR[1]),
                    Number(c.DIRECTION_VECTOR[2]),
                  ] as [number, number, number])
                : undefined,
            }
          : {
              equipment: String(c.EQUIPMENT ?? ""),
              port: String(c.PORT ?? ""),
            },
      ),
    };
  }
  return out;
}

/** The space an equipment is written against (its SPACE_NAME): its own seat --
 * `params.SPACE_NAME`, as set when it was inserted or re-seated (placementSlice,
 * cellsSlice's rehomeEquipmentParams on a move), or loaded from a doc that already
 * named one -- when that name still resolves to a real space cell of its own
 * structure (as its coordinates are relative to that structure); the geometry is
 * used only when the equipment names no seat (or that name no longer resolves --
 * e.g. the space was deleted): the first space under it in plan, among its own
 * structure's when it names one, else the first space of its own structure, else
 * the first space anywhere. */
export function containingCellName(
  cells: Record<string, BuilderCell>,
  eq: BuilderCell,
): string {
  const structure = structureNameToGroup(eq.params ?? {});
  const seatName = eq.params?.SPACE_NAME;
  if (typeof seatName === "string" && seatName) {
    const seat = Object.values(cells).find(
      (c) => c.kind === "cell" && c.name === seatName && (structure === undefined || c.group === structure),
    );
    if (seat) return seat.name;
  }
  const cx = eq.origin[0] + eq.size[0] / 2;
  const cy = eq.origin[1] + eq.size[1] / 2;
  for (const c of Object.values(cells)) {
    if (c.kind !== "cell") continue;
    if (structure !== undefined && c.group !== structure) continue;
    const inX = cx >= c.origin[0] && cx <= c.origin[0] + c.size[0];
    const inY = cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
    if (inX && inY) return c.name;
  }
  // No plan hit: stay within its own structure before falling back to any space at all.
  if (structure !== undefined) {
    const ownFirst = Object.values(cells).find((c) => c.kind === "cell" && c.group === structure);
    if (ownFirst) return ownFirst.name;
  }
  const first = Object.values(cells).find((c) => c.kind === "cell");
  return first ? first.name : "NoSpace";
}
