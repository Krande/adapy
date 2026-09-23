// The flat list the Joints tree is navigated by.
//
// A tree with arrow keys needs ONE ordered list of what is currently on screen: "next row" is
// otherwise a question about a nested structure, answered differently by the renderer and by the
// key handler, and the cursor then skips or sticks on rows depending on which of the two was
// right. So the rows are flattened once, the list is what renders, and Up/Down is an index step
// in it. Pure, so the navigation rules are testable without a DOM.

import type { ClashResult } from "@/state/clashCheckStore";

export type JointRow =
  | { readonly kind: "group"; readonly id: string; readonly typeKey: string }
  | { readonly kind: "joint"; readonly id: string; readonly typeKey: string; readonly jointId: string };

/** Group headers, with the joints of the OPEN group inlined under it. One group opens at a time:
 *  the panel is a narrow column, and two open groups of 48 rows make the second unreachable
 *  without scrolling past the first. */
export function visibleRows(result: ClashResult, openGroup: string | null): readonly JointRow[] {
  const rows: JointRow[] = [];
  for (const group of result.groups) {
    rows.push({ kind: "group", id: `g:${group.typeKey}`, typeKey: group.typeKey });
    if (group.typeKey !== openGroup) continue;
    for (const jointId of group.jointIds) {
      rows.push({ kind: "joint", id: `j:${jointId}`, typeKey: group.typeKey, jointId });
    }
  }
  return rows;
}

/** Index of the row the cursor is on.
 *
 *  The cursor is its OWN state, not a synonym for "the open group": moving it must not expand
 *  anything, or Up/Down cannot pass a group without walking every joint inside it. So `cursorId`
 *  is what the user last moved to, and the rest is fallback for when that row is no longer on
 *  screen (its group was collapsed) or has not been set yet.
 */
export function cursorIndex(
  rows: readonly JointRow[],
  cursorId: string | null,
  openGroup: string | null,
  focusedJoint: string | null,
): number {
  if (cursorId) {
    const i = rows.findIndex((r) => r.id === cursorId);
    if (i >= 0) return i;
    // The row the cursor was on is no longer on screen (its group was collapsed). Fall through to
    // the answers below rather than guessing at a nearby row: what the user can still see is the
    // group that IS open, and landing there is predictable.
  }
  if (focusedJoint) {
    const i = rows.findIndex((r) => r.kind === "joint" && r.jointId === focusedJoint);
    if (i >= 0) return i;
  }
  if (openGroup) {
    const i = rows.findIndex((r) => r.kind === "group" && r.typeKey === openGroup);
    if (i >= 0) return i;
  }
  return -1;
}

/** The group row a row belongs to -- itself for a group, its owner for a joint. */
export function groupRowFor(rows: readonly JointRow[], row: JointRow): JointRow | null {
  if (row.kind === "group") return row;
  return rows.find((r) => r.kind === "group" && r.typeKey === row.typeKey) ?? null;
}

/** Where Up/Down lands. Clamped rather than wrapped: wrapping from the last joint back to the
 *  first group reads as a jump to somewhere else in the model, which is exactly what a person
 *  stepping through joints one at a time is not asking for. */
export function step(rows: readonly JointRow[], from: number, delta: number): JointRow | null {
  if (rows.length === 0) return null;
  if (from < 0) return rows[delta > 0 ? 0 : rows.length - 1];
  const next = Math.min(rows.length - 1, Math.max(0, from + delta));
  return rows[next];
}
