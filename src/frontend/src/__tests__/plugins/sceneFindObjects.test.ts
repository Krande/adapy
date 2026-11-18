// `ctx.scene.findObjects` (plugin API 1.28.0): the objects of the loaded models called one of a set of
// names, as a pick names them (model, id, name, source), read from the Scene tree -- for a plugin that
// knows objects by name and wants to highlight them.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import { useTreeViewStore } from "../../state/treeViewStore";
import { findObjectsByName } from "../../utils/tree_view/modelSource";

const node = (id: string, name: string, extra: Partial<TreeNodeData> = {}, children: TreeNodeData[] = []): TreeNodeData =>
  ({ id, name, children, ...extra }) as TreeNodeData;

function twoModels(): void {
  useTreeViewStore.getState().setTreeData(
    node("__roots__", "roots", {}, [
      node("1", "frame", { model_key: "m1", source_name: "frame.glb" }, [
        node("2", "B1", { model_key: "m1", rangeId: "0" }),
        node("3", "group", { model_key: "m1" }, [node("4", "B2", { model_key: "m1", rangeId: "1" })]),
      ]),
      node("5", "other", { model_key: "m2", source_name: "other.glb" }, [
        node("6", "B1", { model_key: "m2", rangeId: "7" }),
      ]),
    ]),
  );
}

afterEach(() => useTreeViewStore.getState().clearTreeData());

test("finds every object of that name, in tree order, with its model, id and source", () => {
  twoModels();
  assert.deepEqual(findObjectsByName(["B1", "B2"]), [
    { model: "m1", id: "0", name: "B1", sourceName: "frame.glb" },
    { model: "m1", id: "1", name: "B2", sourceName: "frame.glb" },
    { model: "m2", id: "7", name: "B1", sourceName: "other.glb" },
  ]);
});

test("with a source, only the objects of the model loaded from it", () => {
  twoModels();
  assert.deepEqual(findObjectsByName(["B1"], { sourceName: "other.glb" }), [
    { model: "m2", id: "7", name: "B1", sourceName: "other.glb" },
  ]);
  assert.deepEqual(findObjectsByName(["B1"], { sourceName: "absent.glb" }), []);
});

test("a row with no draw range (a group) is not an object; no names, nothing", () => {
  twoModels();
  assert.deepEqual(findObjectsByName(["group", "frame"]), []);
  assert.deepEqual(findObjectsByName([]), []);
  useTreeViewStore.getState().clearTreeData();
  assert.deepEqual(findObjectsByName(["B1"]), [], "no tree: nothing found");
});
