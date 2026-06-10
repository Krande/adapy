/** buildRangeIndex: selection-sync identity must be (model_key, rangeId) — the
 * unique numeric node id per loaded model — never the display name, which repeats
 * thousands of times in real CAD models (and rangeIds restart at 0 per GLB). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildRangeIndex } from "../../state/treeViewStore";
import type { TreeNodeData } from "../../components/tree_view/CustomNode";

function node(
  id: string,
  name: string,
  extra: Partial<TreeNodeData> = {},
  children: TreeNodeData[] = [],
): TreeNodeData {
  return { id, name, children, ...extra };
}

test("duplicate names across two models resolve per (model_key, rangeId)", () => {
  // Two loaded models, both numbering nodes from 0 and both full of nodes
  // named "Solid1".
  const modelA = node("1", "a.glb", { model_key: null }, [
    node("2", "Solid1", { model_key: "A", rangeId: "0", node_name: "node0" }),
    node("3", "Solid1", { model_key: "A", rangeId: "1", node_name: "node0" }),
  ]);
  const modelB = node("4", "b.glb", { model_key: null }, [
    node("5", "Solid1", { model_key: "B", rangeId: "0", node_name: "node0" }),
  ]);
  const root = node("0", "__roots__", {}, [modelA, modelB]);

  const index = buildRangeIndex(root);

  // Group/container nodes (no rangeId/model_key) are not indexed.
  assert.equal(index.size, 3);

  // Same display name, same rangeId, different models — distinct tree rows.
  assert.equal(index.get("A|0")!.id, "2");
  assert.equal(index.get("A|1")!.id, "3");
  assert.equal(index.get("B|0")!.id, "5");
  assert.equal(index.get("A|2"), undefined);

  // Values are live references, not copies.
  assert.equal(index.get("A|0"), modelA.children[0]);
});
