/**
 * The Scene tree's pick -> row index, per loaded model: each model is indexed once, when it arrives,
 * not again on every later load; a relabel keeps the cache; two roots under one model key (a
 * reload) do not write into each other's cached index.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import { indexByModel, useTreeViewStore } from "../../state/treeViewStore";

const leaf = (id: string, model: string, rangeId: string): TreeNodeData => ({ id, name: id, children: [], model_key: model, rangeId });
const root = (id: string, model: string, leaves: TreeNodeData[]): TreeNodeData => ({ id, name: id, children: leaves, model_key: model });
const container = (roots: TreeNodeData[]): TreeNodeData => ({ id: "__roots__", name: "", children: roots });

test("a node is found by its model and range id", () => {
  const a = root("a", "m1", [leaf("a1", "m1", "7")]);
  const b = root("b", "m2", [leaf("b1", "m2", "7")]);
  useTreeViewStore.getState().setTreeData(container([a, b]));
  const find = useTreeViewStore.getState().findNodeByRangeId;
  assert.equal(find("m1", "7")?.id, "a1");
  assert.equal(find("m2", "7")?.id, "b1", "range ids repeat across models; the model keeps them apart");
  assert.equal(find("m3", "7"), null);
});

test("a model already loaded is not walked again when another arrives", () => {
  const a = root("a", "m1", [leaf("a1", "m1", "1")]);
  const first = indexByModel(container([a]));
  // Mutate a's subtree behind the cache's back: a re-walk would see it, a cache hit will not.
  a.children.push(leaf("a2", "m1", "2"));
  const second = indexByModel(container([{ ...a, name: "relabelled" }, root("b", "m2", [leaf("b1", "m2", "1")])]));
  assert.equal(second.get("m1"), first.get("m1"), "the same cached index, through a relabelled copy of the root");
  assert.equal(second.get("m1")?.has("m1|2"), false);
  assert.ok(second.get("m2")?.has("m2|1"));
});

test("two roots under one model key merge without editing each other's cache", () => {
  const r1 = root("r1", "m1", [leaf("x", "m1", "1")]);
  const r2 = root("r2", "m1", [leaf("y", "m1", "2")]);
  const both = indexByModel(container([r1, r2]));
  assert.deepEqual([...both.get("m1")!.keys()].sort(), ["m1|1", "m1|2"]);
  const alone = indexByModel(container([r1]));
  assert.deepEqual([...alone.get("m1")!.keys()], ["m1|1"], "r1's cached index did not absorb r2's entries");
});
