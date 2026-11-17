// The selection tree's drawer is never narrower than its own header (components/tree_view).
import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_WIDTH, MIN_WIDTH, drawerMinWidth, headerContentWidth } from "@/components/tree_view/treeDrawerWidth";

test("the drawer's least width is its header's content when that is wider than its own least", () => {
  // Frozen, no server: a title and the close button fit the drawer's own least.
  assert.equal(drawerMinWidth(headerContentWidth([80, 0, 28], 16)), MIN_WIDTH);
  // Unfrozen with three tabs: tabs, the three panel actions (float, move, window) and close.
  const unfrozen = headerContentWidth([208, 92, 28], 16);
  assert.equal(unfrozen, 344);
  assert.equal(drawerMinWidth(unfrozen), 344);
  assert.ok(drawerMinWidth(unfrozen) > 256, "wider than the drawer's default width: the buttons were outside it");
  // A fraction of a pixel is not lost, and it never asks for more than the drawer may be dragged to.
  assert.equal(drawerMinWidth(300.2), 301);
  assert.equal(drawerMinWidth(9000), MAX_WIDTH);
});
