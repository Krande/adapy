/**
 * Models that arrive while an earlier one's tree is still being built (a bulk load puts one model in
 * the scene after another without waiting for the trees) each keep their root in the Scene tree, with
 * ids of their own -- so a click on an object of any of them is named, by either viewport, and a
 * plugin holding the scene's clicks is told the object's name and its model's source.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import { addModelTree } from "../../state/model_worker/addModelTree";
import { useTreeViewStore } from "../../state/treeViewStore";
import { describeObject } from "../../utils/tree_view/modelSource";

/** The worker's tree build, held until `release`: a model root and one member under it, numbered
 *  from the id it is handed. */
function heldBuild(model: string, member: string) {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const starts: number[] = [];
  const build = async (startId: number): Promise<TreeNodeData> => {
    starts.push(startId);
    await gate;
    return {
      id: String(startId),
      name: "model",
      model_key: null,
      children: [{ id: String(startId + 1), name: member, model_key: model, rangeId: "0", node_name: "node0", children: [] }],
    } as TreeNodeData;
  };
  return { build, release, starts };
}

/** The ids of every row of the tree. */
function rowIds(): string[] {
  const out: string[] = [];
  const stack = [...(useTreeViewStore.getState().treeData?.children ?? [])];
  for (let n = stack.pop(); n; n = stack.pop()) {
    out.push(n.id);
    stack.push(...n.children);
  }
  return out;
}

test("a model whose tree is built while another's is: both are in the tree, each object named", async () => {
  useTreeViewStore.getState().clearTreeData();
  useTreeViewStore.getState().setMaxId(0);
  const first = heldBuild("native-1", "B1");
  const second = heldBuild("native-2", "B2");
  // The second model is put in the scene before the first one's tree is there.
  const a = addModelTree("native-1", first.build, "assets:p/c/s@1#n1");
  const b = addModelTree("native-2", second.build, "assets:p/c/s@1#n2");
  first.release();
  await new Promise((r) => setTimeout(r, 0));
  second.release();
  await Promise.all([a, b]);

  // The rows the pick on each member is looked up by carry the model they are in.
  const roots = useTreeViewStore.getState().treeData?.children ?? [];
  assert.deepEqual(roots.map((r) => r.model_key), ["native-1", "native-2"], "both models have a root");

  assert.deepEqual(describeObject("native-1", "0"), { name: "B1", sourceName: "assets:p/c/s@1#n1" });
  assert.deepEqual(describeObject("native-2", "0"), { name: "B2", sourceName: "assets:p/c/s@1#n2" });
  const ids = rowIds();
  assert.equal(new Set(ids).size, ids.length, `every row has an id of its own: ${ids.join(", ")}`);
});
