/**
 * Loft member -> band CELL REGENERATION.
 *
 * Owns: turning one edited loft member back into its `loft` band cells with
 * stable ids (so a live selection and gizmo survive a shape edit), replacing a
 * member in the raw array, and re-homing a selection that a structural edit
 * dropped. Pure helpers the loft slice calls; they never touch the store.
 */

import {bandBounds, memberToBands, type LoftMemberDoc} from "@/utils/cellbuilder/loft";
import type {BuilderCell} from "./types";
import type {CellBuilderState} from "./state";
import {nextId} from "./shared";

/** Rebuild ONE member's band `loft` cells (Phase 3a edit) into a new cells map,
 * leaving all other cells (and other members' bands) untouched. Existing bay
 * cell ids are preserved by matching `${NAME}_bay{i}` name — so a param edit or
 * a whole-member move (band count unchanged) keeps every id stable, and the
 * live selection + translate gizmo survive the rebuild. An insert/remove shifts
 * the bay indices, so the shifted bays get fresh ids (the caller re-maps the
 * selection). */
export function regenLoftMemberCells(
  cells: Record<string, BuilderCell>,
  member: LoftMemberDoc,
): Record<string, BuilderCell> {
  const idByName = new Map<string, string>();
  const out: Record<string, BuilderCell> = {};
  for (const [id, c] of Object.entries(cells)) {
    if (c.kind === "loft" && c.loft?.member === member.NAME) {
      idByName.set(c.name, id);
    } else {
      out[id] = c;
    }
  }
  for (const band of memberToBands(member)) {
    const id = idByName.get(band.cellName) ?? nextId();
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
  return out;
}

/** Replace the member named `name` in a loft-members array (identity when
 * absent). */
export function replaceLoftMember(
  members: LoftMemberDoc[],
  name: string,
  next: LoftMemberDoc,
): LoftMemberDoc[] {
  return members.map((m) => (m.NAME === name ? next : m));
}

/** After a structural loft edit (insert/remove) drops/renames the selected bay
 * cell, re-home the selection onto the member's bay at `fallbackBay` (clamped),
 * or clear it. Param edits/moves preserve ids so this is skipped for them. */
export function remapLoftSelection(
  s: CellBuilderState,
  cells: Record<string, BuilderCell>,
  memberName: string,
  fallbackBay: number,
): Partial<CellBuilderState> {
  const sel = s.selection;
  if (sel && cells[sel.cellId]) return {}; // still valid — nothing to do
  const bands = Object.values(cells).filter(
    (c) => c.kind === "loft" && c.loft?.member === memberName,
  );
  const bay = Math.min(Math.max(fallbackBay, 0), bands.length - 1);
  const target = bands.find((c) => c.loft?.bay === bay);
  if (target)
    return {
      selection: { kind: "cell", cellId: target.id },
      selectedCellIds: [target.id],
    };
  return { selection: null, selectedCellIds: [], gizmoMode: "none" };
}
