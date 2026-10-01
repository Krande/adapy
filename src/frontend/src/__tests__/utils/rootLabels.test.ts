/**
 * What a loaded model's root row is called in the Scene tree: its top-level name (what the loader
 * was told, else the model's own real root name, else the source label), or the unique id it was
 * loaded under -- distinct either way.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import { distinctLabel, labelRoots, topLevelName } from "../../utils/tree_view/rootLabels";

test("the name a caller gives wins; then the model's own root name, unless it is a generic one", () => {
  assert.equal(topLevelName("/SITE-NORTH", "root", "fn-0f3b@20261001T0724"), "/SITE-NORTH");
  assert.equal(topLevelName(undefined, "/SITE-A", "site-0.glb"), "/SITE-A");
  assert.equal(topLevelName("  ", "Scene", "site-0.glb"), "site-0.glb", "blank and generic fall through");
  assert.equal(topLevelName(null, "", "model.glb"), "model.glb");
});

test("labels stay distinct: -2, -3 after the first", () => {
  assert.equal(distinctLabel("a", new Set()), "a");
  assert.equal(distinctLabel("a", new Set(["a", "a-2"])), "a-3");
});

const root = (id: string, top: string, source: string): TreeNodeData => ({
  id,
  name: source,
  children: [],
  top_name: top,
  source_label: source,
});

test("labelRoots shows each root's name or its id, in order, distinct, sharing what is below", () => {
  const child: TreeNodeData = { id: "c", name: "beam", children: [] };
  const a = { ...root("1", "/SITE-A", "fn-1@r1"), children: [child] };
  const container: TreeNodeData = {
    id: "__roots__",
    name: "",
    children: [a, root("2", "/SITE-A", "fn-1@r2"), { id: "3", name: "legacy.glb", children: [] }],
  };
  const names = labelRoots(container, "name");
  assert.deepEqual(names.children.map((c) => c.name), ["/SITE-A", "/SITE-A-2", "legacy.glb"]);
  assert.equal(names.children[0].children[0], child, "the subtree is the same object");
  const ids = labelRoots(names, "id");
  assert.deepEqual(ids.children.map((c) => c.name), ["fn-1@r1", "fn-1@r2", "legacy.glb"]);
  assert.equal(container.children[0].name, "fn-1@r1", "the input is not changed");
});
