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
import {normalizeGroups, resolveCellGroup, structureNameToGroup, type CellGroup} from "@/utils/cellbuilder/groups";
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

function extractParams(
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

/** The doc's cell groups (name + per-group blueprint), normalized (blank/dup
 * names dropped). Empty for an ungrouped doc. */
export function groupsFromDoc(doc: ProceduralDoc): CellGroup[] {
  return normalizeGroups(
    (doc.groups ?? []).map((g) => ({
      name: String(g.name ?? ""),
      blueprint: String(g.blueprint ?? ""),
    })),
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
  // Openings are UI-placed as global-coord negative-volume boxes (X/Y/Z/DX/DY/DZ).
  // A locally-placed opening imported from elsewhere without global coords is
  // skipped in the builder (still round-trips through params on commit only if it
  // has coords) — the cellbuilder authors global ones.
  for (const o of (doc as { openings?: Record<string, unknown>[] }).openings ??
    []) {
    if (o.X == null || o.DX == null) continue;
    const id = nextId();
    out[id] = {
      id,
      name: String(o.NAME ?? id),
      kind: "opening",
      subtype:
        o.SUBTYPE === "window" ? "window" : o.SUBTYPE === "opening" ? "opening" : "door",
      origin: [Number(o.X ?? 0), Number(o.Y ?? 0), Number(o.Z ?? 0)],
      size: [Number(o.DX ?? 1), Number(o.DY ?? 1), Number(o.DZ ?? 1)],
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

export function containingCellName(
  cells: Record<string, BuilderCell>,
  eq: BuilderCell,
): string {
  const cx = eq.origin[0] + eq.size[0] / 2;
  const cy = eq.origin[1] + eq.size[1] / 2;
  for (const c of Object.values(cells)) {
    if (c.kind !== "cell") continue;
    const inX = cx >= c.origin[0] && cx <= c.origin[0] + c.size[0];
    const inY = cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
    if (inX && inY) return c.name;
  }
  const first = Object.values(cells).find((c) => c.kind === "cell");
  return first ? first.name : "NoSpace";
}
