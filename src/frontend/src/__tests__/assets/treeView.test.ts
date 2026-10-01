import { strict as assert } from "node:assert";
import { test } from "node:test";

import { buildHierarchy } from "../../assets/hierarchy";
import {
  NO_VIEW,
  displayHierarchy,
  isOutOfScope,
  parseViewDoc,
  parseViewHints,
  resolveTreeView,
  viewDocFor,
} from "../../assets/treeView";

// world -> {site-a -> zone-1 -> beam, site-b, appl (another world-level kind)}
const h = buildHierarchy(
  [
    ["world", null, "WORL"],
    ["site-a", "world", "SITE"],
    ["zone-1", "site-a", "ZONE"],
    ["beam", "zone-1", "GENSEC"],
    ["site-b", "world", "SITE"],
    ["appl", "world", "APPLDW"],
  ].map(([id, parent, kind]) => ({ id: id as string, parent: parent as string | null, data: { kind: kind as string } })),
);

const opts = { searchActive: false, showHidden: false };

test("no settings draws the tree as it is", () => {
  const d = displayHierarchy(h, NO_VIEW, opts);
  assert.deepEqual(d.hierarchy.roots, ["world"]);
  assert.deepEqual(d.hierarchy.childrenOf("world"), ["site-a", "site-b", "appl"]);
});

test("a flattened kind is lifted out and its children take its place", () => {
  const view = resolveTreeView(null, { flattenKinds: ["worl"] });
  const d = displayHierarchy(h, view, opts);
  assert.deepEqual(d.hierarchy.roots, ["site-a", "site-b", "appl"]);
  // Kinds compare case-insensitively: the provider wrote WORL, the hint says worl.
  assert.deepEqual([...d.rootKindCensus], [["SITE", 2], ["APPLDW", 1]]);
});

test("flattening a kind that is not at the top lifts it wherever it is", () => {
  const view = resolveTreeView(null, { flattenKinds: ["ZONE"] });
  const d = displayHierarchy(h, view, opts);
  assert.deepEqual(d.hierarchy.childrenOf("site-a"), ["beam"]);
});

test("the root filter keeps only the allowed top-level kinds, and says how many it hid", () => {
  const view = resolveTreeView(null, { flattenKinds: ["WORL"], rootKinds: ["site"] });
  const d = displayHierarchy(h, view, opts);
  assert.deepEqual(d.hierarchy.roots, ["site-a", "site-b"]);
  assert.equal(d.hiddenRoots, 1);
  assert.equal(d.rootFilterStoodDown, false);
});

test("the root filter stands down rather than empty the tree", () => {
  const view = resolveTreeView(null, { flattenKinds: ["WORL"], rootKinds: ["EQUI"] });
  const d = displayHierarchy(h, view, opts);
  assert.deepEqual(d.hierarchy.roots, ["site-a", "site-b", "appl"]);
  assert.equal(d.rootFilterStoodDown, true);
});

test("the root filter is off while searching", () => {
  const view = resolveTreeView(null, { flattenKinds: ["WORL"], rootKinds: ["SITE"] });
  const d = displayHierarchy(h, view, { searchActive: true, showHidden: false });
  assert.deepEqual(d.hierarchy.roots, ["site-a", "site-b", "appl"]);
});

test("an out-of-scope branch is hidden with everything under it, and shown again on request", () => {
  const view = resolveTreeView({ schema: "ada.assets/view@1", out_of_scope: ["zone-1"] }, null);
  assert.deepEqual(displayHierarchy(h, view, opts).hierarchy.childrenOf("site-a"), []);
  assert.deepEqual(
    displayHierarchy(h, view, { searchActive: false, showHidden: true }).hierarchy.childrenOf("site-a"),
    ["zone-1"],
  );
  assert.equal(isOutOfScope(h, view.outOfScope, "beam"), true);
  assert.equal(isOutOfScope(h, view.outOfScope, "site-b"), false);
});

test("a saved field overrides the provider's; a field the save leaves out keeps it", () => {
  const hints = { flattenKinds: ["WORL"], rootKinds: ["SITE"] };
  const onlyHidden = resolveTreeView({ schema: "ada.assets/view@1", out_of_scope: ["x"] }, hints);
  assert.deepEqual([...onlyHidden.flattenKinds], ["worl"]);
  assert.deepEqual([...(onlyHidden.rootKinds ?? [])], ["site"]);
  assert.equal(onlyHidden.source, "provider");

  const allKinds = resolveTreeView({ schema: "ada.assets/view@1", root_kinds: null }, hints);
  assert.equal(allKinds.rootKinds, null);
  assert.equal(allKinds.source, "saved");
});

test("a saved document round-trips, and writes 'every kind' explicitly", () => {
  const doc = viewDocFor({ flattenKinds: ["WORL"], rootKinds: null, outOfScope: ["b", "a", "b"] });
  assert.equal(doc.root_kinds, null);
  const back = parseViewDoc(JSON.stringify(doc));
  assert.deepEqual(back?.flatten_kinds, ["worl"]);
  assert.equal(back?.root_kinds, null);
  assert.deepEqual(back?.out_of_scope, ["a", "b"]);
});

test("malformed hints and documents read as none rather than breaking the tab", () => {
  for (const raw of [null, 3, "x", [], { flatten_kinds: "WORL" }]) assert.equal(parseViewHints(raw), null);
  for (const raw of [null, "{", "{}", JSON.stringify({ schema: "other" })]) assert.equal(parseViewDoc(raw), null);
  assert.deepEqual(parseViewHints({ flatten_kinds: ["WORL", 3, ""], root_kinds: ["SITE"] }), {
    flattenKinds: ["WORL"],
    rootKinds: ["SITE"],
  });
});
