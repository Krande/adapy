// The geometry overlay and the provider filter's notion of "is or contains geometry".
// The regression it pins: a provider that published only a TREE at the collection root used to
// "cover" every row, so filtering on it matched everything.

import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { AssetView } from "../../assets/assetView";
import { geometryIndex, geometryMark, rollupApplies, rowHasGeometry, rowLoadable } from "../../assets/geometryMarks";
import { buildHierarchy } from "../../assets/hierarchy";
import type { ResolutionMode, WireGeometryRollup } from "../../assets/types";

// root -> {site-a -> zone-1 -> beam, site-b -> zone-2, site-c}
const hierarchy = buildHierarchy(
  [
    ["root", null],
    ["site-a", "root"],
    ["zone-1", "site-a"],
    ["beam", "zone-1"],
    ["site-b", "root"],
    ["zone-2", "site-b"],
    ["site-c", "root"],
  ].map(([id, parent]) => ({ id: id as string, parent: parent as string | null, data: { kind: "", label: id } })),
);

const rev = (provider: string, delivery: string) => ({ revision: "r1", manifest: { provider, delivery } });

/** A view with just what the geometry overlay reads. */
function view(
  subjects: Record<string, Record<string, string>>,
  unexplored: string[] = [],
  mode: ResolutionMode = { kind: "latest" },
): AssetView {
  const resolved = new Map(
    Object.entries(subjects).map(([subject, byProvider]) => {
      const by = new Map(Object.entries(byProvider).map(([p, d]) => [p, rev(p, d)]));
      return [subject, { subject, revision: rev("", "none"), content: null, byProvider: by }] as const;
    }),
  );
  return {
    collection: "coll",
    hierarchy,
    resolution: { subjects: resolved, mode },
    unexplored: new Set(unexplored),
  } as unknown as AssetView;
}

/** The server's roll-up for a collection whose WHOLE tree is: root -> site-c -> (unfetched)
 *  deck-9, with the member provider's build at deck-9 -- below a branch the client never opened. */
const rollup = (collection = "coll"): WireGeometryRollup => ({
  schema: "ada.assets/geometry@1",
  collection,
  index_token: "t",
  providers: {
    meshes: { here: ["zone-1"], below: ["root", "site-a"] },
    members: { here: ["deck-9"], below: ["root", "site-c"] },
  },
  any: { here: ["deck-9", "zone-1"], below: ["root", "site-a", "site-c"] },
});

test("a tree published at the root does not make every row geometry", () => {
  const v = view({ root: { tree: "none", meshes: "none" }, "zone-1": { meshes: "mesh" } });
  const all = geometryIndex(v);
  assert.equal(geometryMark(v, all, "zone-1"), "here");
  assert.equal(geometryMark(v, all, "beam"), "covered");
  assert.equal(geometryMark(v, all, "site-a"), "below");
  assert.equal(geometryMark(v, all, "root"), "below");
  assert.equal(rowHasGeometry(v, all, "site-b"), false, "the tree-only root covers it, but nothing loads");
});

test("per provider: only that provider's geometry counts", () => {
  const v = view({ root: { tree: "none" }, "zone-1": { meshes: "mesh" }, "zone-2": { members: "build" } });
  const meshes = geometryIndex(v, "meshes");
  assert.equal(rowHasGeometry(v, meshes, "site-a"), true);
  assert.equal(rowHasGeometry(v, meshes, "site-b"), false);
  const members = geometryIndex(v, "members");
  assert.equal(rowHasGeometry(v, members, "site-a"), false);
  assert.equal(rowHasGeometry(v, members, "site-b"), true);
  const tree = geometryIndex(v, "tree");
  assert.equal(rowHasGeometry(v, tree, "root"), false, "a tree-only provider matches nothing");
});

test("loadable means geometry at the row or above it -- not merely somewhere below", () => {
  const v = view({ root: { tree: "none" }, "zone-1": { meshes: "mesh" } });
  const meshes = geometryIndex(v, "meshes");
  assert.equal(rowLoadable(v, meshes, "zone-1"), true, "published here");
  assert.equal(rowLoadable(v, meshes, "beam"), true, "covered from above");
  assert.equal(rowLoadable(v, meshes, "site-a"), false, "kept by the filter, but loading it loads nothing");
  assert.equal(rowLoadable(v, geometryIndex(v, "tree"), "zone-1"), false, "a tree-only publish loads nothing");
});

test("an unfetched branch is kept as unknown rather than hidden", () => {
  const v = view({ "zone-1": { meshes: "mesh" } }, ["site-c"]);
  const idx = geometryIndex(v, "meshes");
  assert.equal(geometryMark(v, idx, "site-c"), "unknown");
  assert.equal(rowHasGeometry(v, idx, "site-c"), true);
  assert.equal(idx.source, "client");
});

test("with the server roll-up an unfetched branch is decided: below, or tree", () => {
  // site-b -> zone-2 and site-c are both unopened; only site-c holds geometry (deck-9, unloaded).
  const v = view({ "zone-1": { meshes: "mesh" } }, ["site-b", "zone-2", "site-c"]);
  const any = geometryIndex(v, undefined, rollup());
  assert.equal(any.source, "server");
  assert.equal(geometryMark(v, any, "site-c"), "below", "the roll-up knows what is under it");
  assert.equal(geometryMark(v, any, "site-b"), "tree", "definitive: nothing under it, opened or not");
  assert.equal(geometryMark(v, any, "zone-2"), "tree");
  assert.equal(geometryMark(v, any, "zone-1"), "here");
  assert.equal(geometryMark(v, any, "beam"), "covered");
  assert.equal(geometryMark(v, any, "root"), "below");
});

test("the provider filter with the roll-up drops unfetched non-matching branches", () => {
  const v = view({ "zone-1": { meshes: "mesh" } }, ["site-b", "zone-2", "site-c"]);
  const members = geometryIndex(v, "members", rollup());
  assert.equal(rowHasGeometry(v, members, "site-c"), true);
  assert.equal(rowHasGeometry(v, members, "site-b"), false, "no longer kept just for being unopened");
  assert.equal(rowHasGeometry(v, members, "site-a"), false);
  const meshes = geometryIndex(v, "meshes", rollup());
  assert.equal(rowHasGeometry(v, meshes, "site-c"), false);
  assert.equal(rowHasGeometry(v, meshes, "beam"), true);
  // A provider the roll-up does not list has no geometry anywhere.
  const tree = geometryIndex(v, "tree", rollup());
  assert.equal(tree.source, "server");
  assert.equal(rowHasGeometry(v, tree, "site-b"), false);
  assert.equal(rowHasGeometry(v, tree, "root"), false);
});

test("client-known geometry joins the roll-up's (a publish newer than the roll-up)", () => {
  const v = view({ "zone-2": { members: "build" } }, ["site-c"]);
  const members = geometryIndex(v, "members", rollup());
  assert.ok(members.at.has("zone-2") && members.at.has("deck-9"));
  assert.ok(members.below.has("site-b") && members.below.has("site-c"));
  assert.equal(geometryMark(v, members, "site-b"), "below");
});

test("the roll-up is ignored for another collection and outside `latest`", () => {
  const v = view({ "zone-1": { meshes: "mesh" } }, ["site-c"]);
  assert.equal(geometryIndex(v, undefined, rollup("other")).source, "client");
  const asOf = view({ "zone-1": { meshes: "mesh" } }, ["site-c"], { kind: "as-of", revision: "20260101T000000Z" });
  const idx = geometryIndex(asOf, undefined, rollup());
  assert.equal(idx.source, "client");
  assert.equal(geometryMark(asOf, idx, "site-c"), "unknown");
  assert.equal(rollupApplies(rollup(), "coll", { kind: "latest" }), true);
  assert.equal(rollupApplies(null, "coll", { kind: "latest" }), false);
  assert.equal(rollupApplies(rollup(), "coll", { kind: "run", revision: "20260101T000000Z" }), false);
});
