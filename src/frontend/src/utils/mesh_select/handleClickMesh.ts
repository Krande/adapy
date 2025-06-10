// utils/mesh_select/handleClickMesh.ts
import * as THREE from "three";
import { CustomBatchedMesh } from "./CustomBatchedMesh";
import { useModelState } from "../../state/modelState";
import { useObjectInfoStore } from "../../state/objectInfoStore";
import {queryMeshDrawRange, queryNameFromRangeId} from "./queryMeshDrawRange";
import { perform_selection } from "./perform_selection";
import { useTreeViewStore } from "../../state/treeViewStore";
import { useSelectedObjectStore } from "../../state/useSelectedObjectStore";
import { findNodeById } from "../tree_view/findNodeById";

export async function handleClickMesh(
  intersect: THREE.Intersection,
  event: MouseEvent
): Promise<void> {
  if (event.button === 2) return;

  const mesh = intersect.object as CustomBatchedMesh;
  const faceIndex = intersect.faceIndex ?? 0;
  const shiftKey = event.shiftKey;

  // adjust for any translation
  const translation = useModelState.getState().translation;
  const clickPosition = intersect.point.clone();
  if (translation) clickPosition.sub(translation);
  useObjectInfoStore.getState().setClickCoordinate(clickPosition);

  // ← await the worker lookup
  const meshName = mesh.name; // e.g. "node0"
  let drawRange = await queryMeshDrawRange(mesh.unique_key, meshName, faceIndex);
  if (!drawRange) {
    if (mesh.userData?.node_id) {
      drawRange = await queryMeshDrawRange(mesh.unique_key, `node${mesh.userData?.node_id}`, faceIndex);
      if (drawRange) {
        console.warn(`using node_id ${mesh.userData?.node_id} for draw range lookup`);
      }
    }
    if (!drawRange) {
      console.warn("selected mesh has no draw range");
      return;
    }
  }
  const [rangeId] = drawRange;

  await perform_selection(mesh, shiftKey, rangeId);

  // update object info
  const last_selected = await queryNameFromRangeId(mesh.unique_key, rangeId);
  if (!last_selected) {
    console.warn("selected mesh has no name");
    return;
  }
  useObjectInfoStore.getState().setName(last_selected);

  // update tree selection
  const treeViewStore = useTreeViewStore.getState();
  if (treeViewStore.treeData && treeViewStore.tree && !treeViewStore.isTreeCollapsed) {
    // flag programmatic change
    // @ts-ignore
    treeViewStore.tree.isProgrammaticChange = true;

    const node_ids: string[] = [];
    for (const [m, selectedRanges] of useSelectedObjectStore.getState().selectedObjects) {
      for (const rid of selectedRanges) {
        const nodeName = await queryNameFromRangeId(mesh.unique_key, rid);
        if (!nodeName) continue;
        const node = findNodeById(treeViewStore.treeData, nodeName);
        if (node) node_ids.push(node.id);
      }
    }

    const lastNode = findNodeById(treeViewStore.treeData, last_selected);
    treeViewStore.tree.setSelection({
      ids: node_ids,
      mostRecent: lastNode,
      anchor: lastNode,
    });
    if (lastNode) treeViewStore.tree.scrollTo({ id: lastNode.id });

    // @ts-ignore
    treeViewStore.tree.isProgrammaticChange = false;
  }
}
