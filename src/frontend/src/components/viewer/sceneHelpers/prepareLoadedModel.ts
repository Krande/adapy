// sceneHelpers/prepareLoadedModel.ts
import * as THREE from "three";
import { convert_to_custom_batch_mesh } from "../../../utils/scene/convert_to_custom_batch_mesh";
import { replaceBlackMaterials } from "../../../utils/scene/assignDefaultMaterial";
import { buildTreeFromUserData } from "../../../utils/tree_view/generateTree";
import { ModelState } from "../../../state/modelStore";
import { TreeViewState } from "../../../state/treeViewStore";
import { OptionsState } from "../../../state/optionsStore";
import { AnimationState } from "../../../state/animationStore";

interface PrepareLoadedModelParams {
  scene: THREE.Object3D;
  modelStore: ModelState;
  treeViewStore: TreeViewState;
  optionsStore: OptionsState;
  animationStore: AnimationState;
}

export function prepareLoadedModel({
  scene,
  modelStore,
  treeViewStore,
  optionsStore,
  animationStore,
}: PrepareLoadedModelParams): void {
  modelStore.setUserData(scene.userData);

  const meshesToReplace: { original: THREE.Mesh; parent: THREE.Object3D }[] = [];

  scene.traverse((object) => {
    if (object instanceof THREE.Mesh) {
      meshesToReplace.push({ original: object, parent: object.parent! });
    } else if (object instanceof THREE.LineSegments || object instanceof THREE.Points) {
      object.layers.set(1);
    }
  });

  for (const { original, parent } of meshesToReplace) {
    const meshName = original.name;
    const drawRangesData = scene.userData[`draw_ranges_${meshName}`] as Record<string, [number, number]>;

    const drawRanges = new Map<string, [number, number]>();
    if (drawRangesData) {
      for (const [rangeId, [start, count]] of Object.entries(drawRangesData)) {
        drawRanges.set(rangeId, [start, count]);
      }
    }

    const customMesh = convert_to_custom_batch_mesh(original, drawRanges);

    if (optionsStore.showEdges) {
      const edgeLine = customMesh.get_edge_lines();
      scene.add(edgeLine);
    }

    parent.add(customMesh);
    parent.remove(original);
  }

  replaceBlackMaterials(scene);

  const boundingBox = new THREE.Box3().setFromObject(scene);
  modelStore.setBoundingBox(boundingBox);

  if (!optionsStore.lockTranslation) {
    const center = boundingBox.getCenter(new THREE.Vector3());
    const translation = center.clone().multiplyScalar(-1);
    const minY = boundingBox.min.y;
    const bheight = boundingBox.max.y - minY;
    translation.y = -minY + bheight * 0.05;
    scene.position.add(translation);
    modelStore.setTranslation(translation);
  }

  animationStore.setSelectedAnimation("No Animation");

  const treeData = buildTreeFromUserData(scene.userData);
  if (treeData) treeViewStore.setTreeData(treeData);
}
