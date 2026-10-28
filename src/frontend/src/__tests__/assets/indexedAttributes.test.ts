// `@/assets/indexedAttributes` -- which attribute-index lookup a selected scene-tree row stands for.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { attributeLookupFor, lookupKey } from "@/assets/indexedAttributes";
import { buildTreeIndices } from "@/utils/tree_view/treeGraph";

function n(id: string, name: string, children: TreeNodeData[] = [], extra: Partial<TreeNodeData> = {}): TreeNodeData {
  return { id, name, children, ...extra };
}

function scene() {
  const leaf = n("3", "/A/B/leaf", [], { model_key: "m1", rangeId: "2", node_name: "node0" });
  const level = n("2", "/A/B", [leaf], { model_key: "m1" });
  const assetRoot = n("1", "Relabelled site", [level], {
    model_key: "m1",
    source_name: "assets:prov/coll/subj-1@20260901T100000Z",
  });
  const nodeRoot = n("5", "Relabelled node", [], {
    model_key: "m2",
    source_name: "assets:prov/coll/subj-1@20260901T100000Z#node-7",
  });
  const fileLeaf = n("7", "/A/B/leaf", [], { model_key: "m3", rangeId: "1" });
  const fileRoot = n("6", "plant.ifc", [fileLeaf], { model_key: "m3", source_name: "models/plant.ifc" });
  const container = n("__roots__", "", [assetRoot, nodeRoot, fileRoot]);
  return { idx: buildTreeIndices(container), leaf, level, assetRoot, nodeRoot, fileLeaf, fileRoot };
}

test("a row below an asset's root is looked up by its name in the asset's collection", () => {
  const { idx, leaf, level } = scene();
  assert.deepEqual(attributeLookupFor(leaf, idx, []), { provider: "prov", collection: "coll", by: { name: "/A/B/leaf" } });
  assert.deepEqual(attributeLookupFor(level, idx, [])?.by, { name: "/A/B" });
});

test("an asset's root row is looked up by the node it was loaded as, not its relabelled name", () => {
  const { idx, assetRoot, nodeRoot } = scene();
  assert.deepEqual(attributeLookupFor(assetRoot, idx, [])?.by, { nodeId: "subj-1" });
  assert.deepEqual(attributeLookupFor(nodeRoot, idx, [])?.by, { nodeId: "node-7" });
});

test("the asset browser's record of a load wins over the source name", () => {
  const { idx, leaf } = scene();
  const loaded = [
    {
      sourceName: "assets:prov/coll/subj-1@20260901T100000Z",
      ref: { provider: "other", collection: "Coll2", subject: "subj-1", revision: "20260901T100000Z" },
    },
  ];
  const l = attributeLookupFor(leaf, idx, loaded);
  assert.equal(l?.provider, "other");
  assert.equal(l?.collection, "Coll2");
});

test("a row of a file model has no lookup", () => {
  const { idx, fileLeaf, fileRoot } = scene();
  assert.equal(attributeLookupFor(fileLeaf, idx, []), null);
  assert.equal(attributeLookupFor(fileRoot, idx, []), null);
});

test("lookup keys tell id from name and fold the collection's case", () => {
  const a = lookupKey("shared", { provider: "p", collection: "C", by: { name: "x" } });
  assert.equal(a, lookupKey("shared", { provider: "p", collection: "c", by: { name: "x" } }));
  assert.notEqual(a, lookupKey("shared", { provider: "p", collection: "c", by: { nodeId: "x" } }));
  assert.notEqual(a, lookupKey("project:1", { provider: "p", collection: "c", by: { name: "x" } }));
});
