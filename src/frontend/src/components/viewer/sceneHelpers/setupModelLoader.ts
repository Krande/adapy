import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {prepareLoadedModel} from "./prepareLoadedModel";
import {useModelState} from "../../../state/modelState";
import {useOptionsStore} from "../../../state/optionsStore";
import {useAnimationStore} from "../../../state/animationStore";
import {animationControllerRef, modelKeyMapRef, simulationDataRef} from "../../../state/refs";
import {SimulationDataExtensionMetadata} from "../../../extensions/sim_metadata";
import {FilePurpose} from "../../../flatbuffers/base/file-purpose";
import {cacheAndBuildTree} from "../../../state/model_worker/cacheModelUtils";
import {mapAnimationTargets} from "../../../utils/scene/animations/mapAnimationTargets";
import {buildTreeFromUserData} from "../../../utils/tree_view/generateTree";
import {useTreeViewStore} from "../../../state/treeViewStore";
import {loadGLTF} from "./asyncModelLoader";

export async function setupModelLoaderAsync(
    scene: THREE.Scene,
    modelUrl: string | null,
): Promise<THREE.Group> {
  // 3) prepare & add the model to the scene
    const modelGroup = new THREE.Group();
    if (!modelUrl) return modelGroup;

    // 1) load the GLTF
    const gltf = await loadGLTF(modelUrl);

    const gltf_scene = gltf.scene;
    const animations = gltf.animations;
    const modelStore = useModelState.getState()
    const optionsStore = useOptionsStore.getState()
    const animationStore = useAnimationStore.getState()

    // access the raw JSON
    const sim_ext_data = (gltf as any).parser.json.extensions?.ADA_SIM_data;
    if (sim_ext_data) {
        simulationDataRef.current = sim_ext_data as SimulationDataExtensionMetadata;
        modelStore.model_type = FilePurpose.ANALYSIS;
    } else {
        modelStore.model_type = FilePurpose.DESIGN;
    }

    // once userData is on the scene:
    const rawUD = (gltf_scene.userData ?? {}) as Record<
        string,
        any
    >;

    if (animations.length > 0) {
        // Set the hasAnimation flag to true in the store
        animationStore.setHasAnimation(true);
    }

    const model_hash = await prepareLoadedModel({gltf_scene: gltf_scene});

    // delegate all the caching to our helper
    await cacheAndBuildTree(model_hash, rawUD);

    if (!modelKeyMapRef.current){
        modelKeyMapRef.current = new Map<string, THREE.Object3D>();
    }
    modelKeyMapRef.current.set(model_hash, gltf_scene);

    modelGroup.add(gltf_scene);
    scene.add(modelGroup);
    if (animations.length > 0) {
        animationControllerRef.current?.setMeshMap(mapAnimationTargets(gltf));

        // Add animations to the controller
        animations.forEach((animation) => {
            animationControllerRef.current?.addAnimation(animation);
        });

        // Play the first animation
        animationControllerRef.current?.setCurrentAnimation("No Animation");
    } else {
        animationStore.setHasAnimation(false); // If no animations, set false
    }

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

    return modelGroup;
}
