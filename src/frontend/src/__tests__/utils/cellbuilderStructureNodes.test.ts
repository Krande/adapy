import assert from "node:assert/strict";
import { test } from "node:test";

import { findStructureNode, rootsForSource, type StructureTreeNode } from "../../utils/cellbuilder/structureNodes";

// The Scene tree a pm-engine compile of "Mini Topside With Jacket" produces: the
// assembly root, then one row per structure named after its group.
const node = (id: string, name: string, children: StructureTreeNode[] = [], source_name?: string) => ({
  id,
  name,
  children,
  ...(source_name ? { source_name } : {}),
});

const jacketResult = node(
  "0",
  "Mini Topside With Jacket",
  [
    node("1", "Mini", [node("2", "Topology"), node("3", "Mini_jacket")]), // a deeper namesake
    node("118", "Mini_jacket", [node("119", "legs")]),
  ],
  "procedural:jacket",
);
const otherModel = node("500", "Other", [node("501", "Mini")], "some/file.glb");
const container = node("__roots__", "", [otherModel, jacketResult]);

test("rootsForSource picks the result's root out of every loaded model", () => {
  assert.deepEqual(rootsForSource(container, "procedural:jacket"), [jacketResult]);
  assert.deepEqual(rootsForSource(container, "procedural:nope"), []);
  assert.deepEqual(rootsForSource(jacketResult, "procedural:jacket"), [jacketResult]); // a single root
  assert.deepEqual(rootsForSource(null, "procedural:jacket"), []);
});

test("findStructureNode finds each structure at its own level", () => {
  assert.equal(findStructureNode(jacketResult, "Mini")?.id, "1");
  // The shallow structure row, not the namesake inside Mini.
  assert.equal(findStructureNode(jacketResult, "Mini_jacket")?.id, "118");
});

test("findStructureNode: an excluded (unbuilt) group has no row", () => {
  const onlyMini = node("0", "J", [node("1", "Mini")]);
  assert.equal(findStructureNode(onlyMini, "Mini_jacket"), null);
});
