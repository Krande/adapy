/**
 * Which space an equipment is seated on/over, and keeping its `SPACE_NAME` in
 * sync with where it actually is after a move -- pure functions over the
 * builder's cells, store-independent (mirrors `groups.ts`/`snap.ts`).
 *
 * Equipment's own coordinates (X/Y/Z, GLOBAL_COORDS) are unaffected by
 * `SPACE_NAME`: it is metadata the tree and the Properties panel file the
 * equipment by, the same role a GLOBAL opening's `SPACE_NAME`/`SPACE_SIDE`
 * play (see `cellsSlice.ts`'s `rehomeGlobalOpeningParams`). Left stale after a
 * move, a doc reload re-derives it wrong: `containingCellName` (docCodec.ts)
 * trusts a stored name that still resolves to SOME space, even one the
 * equipment no longer sits over.
 */

import type {BuilderCell} from "@/state/cellbuilder/types";
import {itemStructure} from "./include";
import {structureNameToGroup, type CellGroup} from "./groups";

type LoftMemberRef = {NAME: string; STRUCTURE_NAME?: string};

/** The space `eq` (with no `SPACE_NAME` of its own) is currently seated on, going by `oldLoc`:
 *  among the spaces whose X/Y footprint contains `eq`'s centre -- in its own structure when it
 *  names one, as `containingCellName` also restricts -- the one whose CURRENT-mode surface (its
 *  floor when `oldLoc` is FLOOR, its roof when ROOF) is nearest `eq`'s current Z. Several spaces
 *  can share the same footprint stacked at different heights (a mezzanine deck under a roof
 *  deck), so "the first one found" picked whichever happened to iterate first, not the one the
 *  equipment is actually sitting on. Null with no such space -- the caller then leaves the
 *  equipment where it is and only flips SPACE_LOC. */
export function hostSpaceForLocation(
  cells: Record<string, BuilderCell>,
  eq: BuilderCell,
  oldLoc: "FLOOR" | "ROOF",
): BuilderCell | null {
  const structure = structureNameToGroup(eq.params ?? {});
  const cx = eq.origin[0] + eq.size[0] / 2;
  const cy = eq.origin[1] + eq.size[1] / 2;
  let best: BuilderCell | null = null;
  let bestDist = Infinity;
  for (const c of Object.values(cells)) {
    if (c.kind !== "cell") continue;
    if (structure !== undefined && c.group !== structure) continue;
    const inX = cx >= c.origin[0] && cx <= c.origin[0] + c.size[0];
    const inY = cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
    if (!inX || !inY) continue;
    const surface = oldLoc === "ROOF" ? c.origin[2] + c.size[2] : c.origin[2];
    const dist = Math.abs(surface - eq.origin[2]);
    if (dist < bestDist) {
      best = c;
      bestDist = dist;
    }
  }
  return best;
}

/** Re-home `eq` (already at its new origin) to the space cell its X/Y centre now sits over,
 *  within its own structure -- so a structure's origin can never confuse this for another
 *  structure's cell at the same local coordinates.
 *
 *  The structure is the equipment's own `STRUCTURE_NAME` when it names one, else the structure of
 *  whatever it was ALREADY seated in (`itemStructure`, via its current, possibly stale,
 *  `SPACE_NAME`) -- an equipment dragged across a structure boundary must not suddenly be judged
 *  against every OTHER structure's cells just because it carries no `STRUCTURE_NAME` of its own.
 *
 *  Candidates are the structure's own space cells whose footprint holds the equipment's centre.
 *  If the CURRENT `SPACE_NAME` is among them, it is kept (an explicit seat, or a rider carried
 *  along in a multi-move, must not be re-picked just because another candidate is geometrically
 *  closer); otherwise the candidate whose surface (its floor while SPACE_LOC is FLOOR, its roof
 *  while ROOF) is nearest the equipment's Z wins. With no candidate at all, `SPACE_NAME` is
 *  dropped, as a GLOBAL opening's is when it moves off every space. */
export function rehomeEquipmentParams(
  cells: Record<string, BuilderCell>,
  eq: BuilderCell,
  groups: readonly CellGroup[] = [],
  loftMembers: readonly LoftMemberRef[] = [],
): Record<string, unknown> {
  const structure = structureNameToGroup(eq.params ?? {}) ?? itemStructure(eq, cells, groups, loftMembers);
  const cx = eq.origin[0] + eq.size[0] / 2;
  const cy = eq.origin[1] + eq.size[1] / 2;
  const hosts: BuilderCell[] = [];
  for (const c of Object.values(cells)) {
    if (c.kind !== "cell") continue;
    if (structure !== undefined && c.group !== structure) continue;
    const inX = cx >= c.origin[0] && cx <= c.origin[0] + c.size[0];
    const inY = cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
    if (inX && inY) hosts.push(c);
  }
  const next = {...eq.params};
  if (hosts.length === 0) {
    delete next.SPACE_NAME;
    return next;
  }
  const current = typeof eq.params?.SPACE_NAME === "string" ? eq.params.SPACE_NAME : undefined;
  if (current && hosts.some((c) => c.name === current)) return next;
  const loc = (eq.params?.SPACE_LOC as "FLOOR" | "ROOF" | undefined) ?? "FLOOR";
  const surfaceFor = (c: BuilderCell) => (loc === "ROOF" ? c.origin[2] + c.size[2] : c.origin[2]);
  let best = hosts[0];
  let bestDist = Math.abs(surfaceFor(best) - eq.origin[2]);
  for (const c of hosts.slice(1)) {
    const dist = Math.abs(surfaceFor(c) - eq.origin[2]);
    if (dist < bestDist) {
      best = c;
      bestDist = dist;
    }
  }
  next.SPACE_NAME = best.name;
  return next;
}
