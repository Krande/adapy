import * as THREE from "three";
import {GLTF, GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {prepareLoadedModel} from "./prepareLoadedModel";
import {useModelStore} from "../../../state/modelStore";
import {useOptionsStore} from "../../../state/optionsStore";
import {useTreeViewStore} from "../../../state/treeViewStore";
import {useAnimationStore} from "../../../state/animationStore";
import {animationControllerRef} from "../../../state/refs";
import {FilePurpose} from "../../../flatbuffers/base/file-purpose";

export function mapAnimationTargets(gltf: GLTF): Map<string, string[]> {
    // Access the raw glTF JSON structure
    const json = (gltf as any).parser?.json;
    if (!json) {
        throw new Error('Raw glTF JSON not available on parser.json');
    }

    const animDefs = json.animations as Array<any>;
    const nodeDefs = json.nodes as Array<any>;
    const result = new Map<string, string[]>();

    animDefs.forEach((animDef, idx) => {
        const animName = animDef.name || `animation_${idx}`;
        const targetNames: string[] = [];

        // Each channel references a node index in its target
        animDef.channels.forEach((channel: any) => {
            const nodeIndex = channel.target.node;
            const nodeDef = nodeDefs[nodeIndex];
            const nodeName = nodeDef?.name || `node_${nodeIndex}`;
            if (!targetNames.includes(nodeName)) {
                targetNames.push(nodeName);
            }
        });

        result.set(animName, targetNames);
    });

    return result;
}


export function setupModelLoader(
    scene: THREE.Scene,
    modelUrl: string | null,
): THREE.Group | null {
    if (!modelUrl) return null;
    const modelGroup = new THREE.Group();
    const loader = new GLTFLoader();
    loader.load(
        modelUrl,
        (gltf) => {
            const gltf_scene = gltf.scene;
            const animations = gltf.animations;
            if (animations.length > 0) {
                // Set the hasAnimation flag to true in the store
                useAnimationStore.getState().setHasAnimation(true);
            }
            prepareLoadedModel({
                gltf_scene: gltf_scene,
                modelStore: useModelStore.getState(),
                optionsStore: useOptionsStore.getState(),
                treeViewStore: useTreeViewStore.getState(),
            });

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
                useAnimationStore.getState().setHasAnimation(false); // If no animations, set false
            }
        },

        undefined,
        (error) => {
            console.error("Error loading model:", error);
        },
    );

    return modelGroup;
}
