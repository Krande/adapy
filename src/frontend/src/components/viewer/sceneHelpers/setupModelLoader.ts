import * as THREE from "three";
import {prepareLoadedModel} from "./prepareLoadedModel";
import {useModelState} from "../../../state/modelState";
import {useOptionsStore} from "../../../state/optionsStore";
import {useAnimationStore} from "../../../state/animationStore";
import {animationControllerRef, modelKeyMapRef, sceneRef, simulationDataRef, adaExtensionRef} from "../../../state/refs";
import {SimulationDataExtensionMetadata} from "../../../extensions/design_and_analysis_extension";
import {FilePurpose} from "../../../flatbuffers/base/file-purpose";
import {cacheAndBuildTree} from "../../../state/model_worker/cacheModelUtils";
import {mapAnimationTargets} from "../../../utils/scene/animations/mapAnimationTargets";
import {loadGLTF} from "./asyncModelLoader";
import {AnimationController} from "../../../utils/scene/animations/AnimationController";
import {updateAllPointsSize} from "../../../utils/scene/updatePointSizes";

export async function setupModelLoaderAsync(
    modelUrl: string | null, translate: boolean = true
): Promise<THREE.Group> {
    if (sceneRef.current == null) {
        console.error("Scene reference is null");
        return new THREE.Group();
    }

    const main_scene = sceneRef.current;

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
    const ada_ext_data = (gltf as any).parser.json.extensions?.ADA_EXT_data;
    if (ada_ext_data){
        adaExtensionRef.current = ada_ext_data;
        if (ada_ext_data.simulation_objects.length > 0){
            simulationDataRef.current = ada_ext_data.simulation_objects[0];
        }
    }
    animationControllerRef.current = new AnimationController(main_scene);
    // Handle animations - clear previous state first
    if (animations.length > 0) {
        // Set the hasAnimation flag to true in the store
        animationStore.setHasAnimation(true);

        // Clear previous animations completely
        animationControllerRef.current?.clear();

        // Set the mesh map for the new animations
        animationControllerRef.current?.setMeshMap(mapAnimationTargets(gltf));

        // Add animations to the controller
        animations.forEach((animation) => {
            animationControllerRef.current?.addAnimation(animation);
        });

        // Reset to no animation state - don't call setCurrentAnimation yet
        // We'll do this after the model is fully loaded and added to scene
    } else {
        animationStore.setHasAnimation(false);
        // Clear controller even if no animations to ensure clean state
        animationControllerRef.current?.clear();
    }

    // create a unique hash string
    const model_hash = gltf_scene.name + "_" + gltf_scene.uuid;

    await prepareLoadedModel({gltf_scene: gltf_scene, hash: model_hash});
    // once userData is on the scene:
    const rawUD = (gltf_scene.userData ?? {}) as Record<
        string,
        any
    >;

    // delegate all the caching to our helper
    await cacheAndBuildTree(model_hash, rawUD);

    if (modelStore.translation && translate) {
        console.log("Model already translated");
        gltf_scene.position.add(modelStore.translation);
    } else {
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
    }


    modelGroup.add(gltf_scene);

    main_scene.add(modelGroup);

    // Ensure point sizes and sizing mode are applied after the model is in the scene,
    // so points are visible immediately without needing the Options panel.
    try {
        const ps = optionsStore.pointSize ?? 0.01;
        const abs = (optionsStore as any).pointSizeAbsolute ?? true;
        updateAllPointsSize(ps, abs);
    } catch {}

    if (!modelKeyMapRef.current) {
        modelKeyMapRef.current = new Map<string, THREE.Object3D>();
    }
    modelKeyMapRef.current.set(model_hash, modelGroup);

    if (animations.length > 0) {
        // Set the hasAnimation flag to true in the store
        animationStore.setHasAnimation(true);
    }
    return modelGroup;
}
