// sceneHelpers/prepareLoadedModel.ts
import * as THREE from "three";
import {convert_to_custom_batch_mesh} from "../../../utils/scene/convert_to_custom_batch_mesh";
import {replaceBlackMaterials} from "../../../utils/scene/assignDefaultMaterial";
import {useModelState} from "../../../state/modelState";
import {useOptionsStore} from "../../../state/optionsStore";
import {rendererRef} from "../../../state/refs";
import {FilePurpose} from "../../../flatbuffers/base";
import {useAnimationStore} from "../../../state/animationStore";
import {assignMorphToEdgeAlso} from "../../../utils/scene/animations/assignMorphToEdgeAlso";

interface PrepareLoadedModelParams {
    gltf_scene: THREE.Object3D;
}


export function prepareLoadedModel({gltf_scene}: PrepareLoadedModelParams): void {
    const modelStore = useModelState.getState()
    const optionsStore = useOptionsStore.getState()

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
        if (useAnimationStore.getState().hasAnimation && modelStore.model_type == FilePurpose.ANALYSIS) {
            const line_geo = original.children[0] as THREE.LineSegments;
            try {
                assignMorphToEdgeAlso(customMesh, line_geo);
            } catch (e) {
                console.error("Error assigning morph to edge:", e);
            }

            parent.add(line_geo)
        }
        parent.remove(original);
    }

    replaceBlackMaterials(gltf_scene);
}
