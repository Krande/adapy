// handleMeshSelectionCore.ts
import * as THREE from "three";
import { CustomBatchedMesh } from "./CustomBatchedMesh";
import { getSelectedMeshDrawRange } from "./getSelectedMeshDrawRange";
import { useModelState } from "../../state/modelState";
import { useObjectInfoStore } from "../../state/objectInfoStore";
import { useSelectedObjectStore } from "../../state/useSelectedObjectStore";
import { useTreeViewStore } from "../../state/treeViewStore";
import { findNodeById } from "../tree_view/findNodeById";
import { perform_selection } from "./perform_selection";

export function handleMeshSelectionCore(params: {
  object: THREE.Object3D;
  faceIndex?: number;
  point: THREE.Vector3;
  shiftKey: boolean;
}) {
  const mesh = params.object as CustomBatchedMesh;
  const faceIndex = params.faceIndex ?? 0;
  const shiftKey = params.shiftKey;

  const translation = useModelState.getState().translation;
  const clickPosition = params.point.clone();

  if (translation) {
    clickPosition.sub(translation);
  }

  useObjectInfoStore.getState().setClickCoordinate(clickPosition);

  const drawRange = getSelectedMeshDrawRange(mesh, faceIndex);
  if (!drawRange) {
    console.warn("selected mesh has no draw range");
    return;
  }

  const [rangeId] = drawRange;
  perform_selection(mesh, shiftKey, rangeId);

  const userdata = useModelState.getState().userdata;
  const hierarchy: Record<string, [string, string | number]> = userdata["id_hierarchy"];
  const [last_selected] = hierarchy[rangeId];

  useObjectInfoStore.getState().setName(last_selected);

  const treeViewStore = useTreeViewStore.getState();
  if (treeViewStore.treeData && treeViewStore.tree && !treeViewStore.isTreeCollapsed) {
    // @ts-ignore
    treeViewStore.tree.isProgrammaticChange = true;

    const node_ids: string[] = [];

    for (let [mesh, selectedRanges] of useSelectedObjectStore.getState().selectedObjects) {
      for (let rangeId of selectedRanges) {
        const [nodeName] = hierarchy[rangeId];
        const selectedNode = findNodeById(treeViewStore.treeData, nodeName);
        if (selectedNode) {
          node_ids.push(selectedNode.id);
        }
      }
    }

    const last_selected_node = findNodeById(treeViewStore.treeData, last_selected);
    treeViewStore.tree.setSelection({
      ids: node_ids,
      mostRecent: last_selected_node,
      anchor: last_selected_node,
    });

    if (last_selected_node) {
      treeViewStore.tree.scrollTo({ id: last_selected_node.id });
    }

    // @ts-ignore
    treeViewStore.tree.isProgrammaticChange = false;
  }
}
