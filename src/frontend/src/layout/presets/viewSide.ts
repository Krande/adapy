// Which side of a divider the 3D view is on, so a divider beside a panel held at a px
// size resizes the panel whose edge FACING THE VIEW it is: in a column of panels right
// of the view, a divider is the left edge of the panel after it; left of the view, the
// right edge of the panel before it. "This panel gets wider", with the view giving the
// room, for every edge the user can reach - the far one of a column at the screen edge
// is not one.
//
// Pure, so it runs under `node --test`; the grid passes the side down through its splits.

export type ViewSide = "before" | "after";

/** The view's side, along each direction, of the split a node sits in. */
export interface ViewSides {
  row?: ViewSide;
  col?: ViewSide;
}

interface Node {
  kind: string;
  children?: readonly Node[];
}

export function containsViewport(node: Node): boolean {
  return node.kind === "viewport" || (node.children ?? []).some(containsViewport);
}

/** Where the view is relative to the divider after child `i`: from the children holding it,
 *  else from the side inherited along this split's direction (undefined: no view either way). */
export function viewSideOfDivider(children: readonly Node[], i: number, inherited?: ViewSide): ViewSide | undefined {
  const at = children.findIndex(containsViewport);
  if (at < 0) return inherited;
  return at <= i ? "before" : "after";
}

/** The sides child `j` of a split along `dir` passes on: along `dir` from where the view sits
 *  among its siblings (inherited when none holds it), across `dir` unchanged. */
export function childViewSides(children: readonly Node[], dir: "row" | "col", j: number, inherited: ViewSides): ViewSides {
  const at = children.findIndex(containsViewport);
  const along = at < 0 || at === j ? (at === j ? undefined : inherited[dir]) : at < j ? "before" : "after";
  return {...inherited, [dir]: along};
}

/** Which neighbour of the divider after child `i` it resizes, of the two that may be held at
 *  a px size: the one after it when the view is before it, else the one before it. */
export function preferAfter(side: ViewSide | undefined): boolean {
  return side === "before";
}
