// Keyboard navigation and range selection over the Sources tree, as plain functions of its rows.
//
// The tree is virtualised and FLAT -- `flattenVisible` hands it the visible rows in order with
// their depth -- so "next", "parent" and "from here to there" are all questions about that list,
// and answering them here keeps the component a thin binding of keys to store calls.
//
// The keys are the WAI-ARIA tree pattern's: Up/Down move, Home/End jump, Right opens a closed
// branch or steps into an open one, Left closes an open branch or steps out to the parent.
// Shift with a move extends the selection from the anchor instead of replacing it.

export interface KeyRow {
  readonly id: string;
  readonly depth: number;
  /** Can open: has children drawn, or a level still to fetch below it. */
  readonly hasChildren: boolean;
  /** Open by the user's own choice (not merely held open by a search). */
  readonly expanded: boolean;
}

export type TreeKeyAction =
  /** Move focus to `id`; with `extend`, select from the anchor to it. */
  | { readonly kind: "focus"; readonly id: string; readonly extend: boolean }
  | { readonly kind: "expand"; readonly id: string }
  | { readonly kind: "collapse"; readonly id: string };

/** What `key` does with focus on `focus` (null: nothing focused yet). Null when the key is not
 *  the tree's, or does nothing here -- the caller then leaves the event alone. */
export function treeKeyAction(
  rows: readonly KeyRow[],
  focus: string | null,
  key: string,
  shift: boolean,
): TreeKeyAction | null {
  if (!rows.length) return null;
  const at = focus === null ? -1 : rows.findIndex((r) => r.id === focus);
  const move = (i: number): TreeKeyAction | null => {
    const to = Math.max(0, Math.min(rows.length - 1, i));
    return to === at ? null : { kind: "focus", id: rows[to].id, extend: shift };
  };
  switch (key) {
    case "ArrowDown":
      return move(at + 1);
    case "ArrowUp":
      // From nothing focused, Up starts at the top as Down does.
      return move(at < 0 ? 0 : at - 1);
    case "Home":
      return move(0);
    case "End":
      return move(rows.length - 1);
  }
  if (at < 0) return key === "ArrowRight" || key === "ArrowLeft" ? move(0) : null;
  const row = rows[at];
  if (key === "ArrowRight") {
    if (!row.hasChildren) return null;
    if (!row.expanded) return { kind: "expand", id: row.id };
    // Open already: into its first child -- the next row, when it is one level deeper. (A level
    // still loading has no rows yet; nothing to step into.)
    const next = rows[at + 1];
    return next && next.depth > row.depth ? { kind: "focus", id: next.id, extend: false } : null;
  }
  if (key === "ArrowLeft") {
    if (row.hasChildren && row.expanded) return { kind: "collapse", id: row.id };
    const parent = parentIndex(rows, at);
    return parent < 0 ? null : { kind: "focus", id: rows[parent].id, extend: false };
  }
  return null;
}

/** The nearest row above `at` that is shallower: its parent as drawn. -1 at the top level. */
function parentIndex(rows: readonly KeyRow[], at: number): number {
  for (let i = at - 1; i >= 0; i--) if (rows[i].depth < rows[at].depth) return i;
  return -1;
}

/** The rows an action on `id` applies to: the whole selection when `id` is part of a selection of
 *  several, else `id` alone -- a click outside the selection acts on what was clicked, as in a file
 *  manager. Only the TOPMOST of them: a selected row under another selected row is already part of
 *  that one's subtree, and loading or requesting it again would put its geometry in twice. In
 *  selection order. */
export function actionTargets(
  selection: ReadonlySet<string>,
  id: string,
  parentOf: (id: string) => string | null | undefined,
): string[] {
  if (selection.size < 2 || !selection.has(id)) return [id];
  const covered = (n: string): boolean => {
    for (let p = parentOf(n); p; p = parentOf(p)) if (selection.has(p)) return true;
    return false;
  };
  return [...selection].filter((n) => !covered(n));
}

/** The visible rows from `anchor` to `focus`, inclusive, in tree order. Just `focus` when the
 *  anchor is not visible -- collapsed away, or never set -- since a range needs both ends. */
export function rangeIds(rows: readonly { readonly id: string }[], anchor: string | null, focus: string): string[] {
  const b = rows.findIndex((r) => r.id === focus);
  const a = anchor === null ? -1 : rows.findIndex((r) => r.id === anchor);
  if (a < 0 || b < 0) return [focus];
  const [lo, hi] = a <= b ? [a, b] : [b, a];
  return rows.slice(lo, hi + 1).map((r) => r.id);
}
