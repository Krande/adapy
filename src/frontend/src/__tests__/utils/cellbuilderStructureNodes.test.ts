import assert from "node:assert/strict";
import { test } from "node:test";

import { findStructureNode, rootsForSource, type StructureTreeNode } from "../../utils/cellbuilder/structureNodes";

// The Scene tree a grouping engine's compile of a two-structure model produces: the
// assembly root, then one row per structure named after its group.
const node = (id: string, name: string, children: StructureTreeNode[] = [], source_name?: string) => ({
  id,
  name,
  children,
  ...(source_name ? { source_name } : {}),
});

const twoStructureResult = node(
  "0",
  "Two structures",
  [
    node("1", "upper", [node("2", "Topology"), node("3", "lower")]), // a deeper namesake
    node("118", "lower", [node("119", "legs")]),
  ],
  "procedural:two-structures",
);
const otherModel = node("500", "Other", [node("501", "upper")], "some/file.glb");
const container = node("__roots__", "", [otherModel, twoStructureResult]);

test("rootsForSource picks the result's root out of every loaded model", () => {
  assert.deepEqual(rootsForSource(container, "procedural:two-structures"), [twoStructureResult]);
  assert.deepEqual(rootsForSource(container, "procedural:nope"), []);
  assert.deepEqual(rootsForSource(twoStructureResult, "procedural:two-structures"), [twoStructureResult]); // a single root
  assert.deepEqual(rootsForSource(null, "procedural:two-structures"), []);
});

test("findStructureNode finds each structure at its own level", () => {
  assert.equal(findStructureNode(twoStructureResult, "upper")?.id, "1");
  // The shallow structure row, not the namesake inside upper.
  assert.equal(findStructureNode(twoStructureResult, "lower")?.id, "118");
});

test("findStructureNode: an excluded (unbuilt) group has no row", () => {
  const onlyUpper = node("0", "J", [node("1", "upper")]);
  assert.equal(findStructureNode(onlyUpper, "lower"), null);
});
