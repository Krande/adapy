// sceneHelpers/prepareLoadedModel.ts
import * as THREE from "three";
import {convert_to_custom_batch_mesh} from "../../../utils/scene/convert_to_custom_batch_mesh";
import {replaceBlackMaterials} from "../../../utils/scene/assignDefaultMaterial";
import {buildTreeFromUserData} from "../../../utils/tree_view/generateTree";
import {ModelState} from "../../../state/modelStore";
import {TreeViewState} from "../../../state/treeViewStore";
import {OptionsState} from "../../../state/optionsStore";
import {rendererRef} from "../../../state/refs";
import {FilePurpose} from "../../../flatbuffers/base";
import {useAnimationStore} from "../../../state/animationStore";
import {assignMorphToEdgeAlso} from "../../../utils/scene/animations/assignMorphToEdgeAlso";

interface PrepareLoadedModelParams {
    gltf_scene: THREE.Object3D;
    modelStore: ModelState;
    treeViewStore: TreeViewState;
    optionsStore: OptionsState;
}


export function prepareLoadedModel({
                                       gltf_scene,
                                       modelStore,
                                       treeViewStore,
                                       optionsStore,
                                   }: PrepareLoadedModelParams): void {
    modelStore.setUserData(gltf_scene.userData);
    // we'll collect all edge geometries here
    const meshesToReplace: { original: THREE.Mesh; parent: THREE.Object3D }[] = [];

    gltf_scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
            meshesToReplace.push({original: object, parent: object.parent!});
        } else if (object instanceof THREE.LineSegments || object instanceof THREE.Points) {
            object.layers.set(1);
        }
    });

    for (const {original, parent} of meshesToReplace) {
        const meshName = original.name;
        const drawRangesData = gltf_scene.userData[`draw_ranges_${meshName}`] as Record<string, [number, number]>;

        const drawRanges = new Map<string, [number, number]>();
        if (drawRangesData) {
            for (const [rangeId, [start, count]] of Object.entries(drawRangesData)) {
                drawRanges.set(rangeId, [start, count]);
            }
        }

        const customMesh = convert_to_custom_batch_mesh(original, drawRanges);

        if (optionsStore.showEdges && drawRanges.size && modelStore.model_type == FilePurpose.DESIGN) {
            if (rendererRef.current)
                parent.add(customMesh.getEdgeOverlay(rendererRef.current));
        }

        parent.add(customMesh);
        if (useAnimationStore.getState().hasAnimation && modelStore.model_type == FilePurpose.ANALYSIS){
            const line_geo = original.children[0] as THREE.LineSegments;
            assignMorphToEdgeAlso(customMesh, line_geo);
            parent.add(line_geo)
        }
        parent.remove(original);
    }

    replaceBlackMaterials(gltf_scene);

    const boundingBox = new THREE.Box3().setFromObject(gltf_scene);
    modelStore.setBoundingBox(boundingBox);

    if (!optionsStore.lockTranslation && modelStore.model_type == FilePurpose.DESIGN) {
        const center = boundingBox.getCenter(new THREE.Vector3());
        const translation = center.clone().multiplyScalar(-1);
        if (modelStore.zIsUp) {
            const minZ = boundingBox.min.z;
            const bheight = boundingBox.max.z - minZ;
            translation.z = -minZ + bheight * 0.05;
        } else {
            const minY = boundingBox.min.y;
            const bheight = boundingBox.max.y - minY;
            translation.y = -minY + bheight * 0.05;
        }

        gltf_scene.position.add(translation);
        modelStore.setTranslation(translation);
    }

    const treeData = buildTreeFromUserData(gltf_scene.userData);
    if (treeData) treeViewStore.setTreeData(treeData);
}
