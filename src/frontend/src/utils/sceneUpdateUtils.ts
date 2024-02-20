// SceneUpdateUtils.ts
import * as THREE from 'three';
import {GLTFLoader} from 'three/examples/jsm/loaders/GLTFLoader';
import {SceneAction} from "./handleWebSocketMessage";

// Function to load GLTF models
const loadGLTFModel = async (url: string): Promise<THREE.Scene> => {
    return new Promise((resolve, reject) => {
        const loader = new GLTFLoader();
        // @ts-ignore
        loader.load(url, (gltf) => resolve(gltf.scene), undefined, reject);
    });
};

// Update Scene Function
export const updateScene = async (
    currentScene: THREE.Scene,
    eventType: SceneAction,
    modelUrl: string,
    targetId?: string
): Promise<string> => {
    const newScene = await loadGLTFModel(modelUrl);
    newScene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
            object.material.side = THREE.DoubleSide;
        }
    });

    switch (eventType) {
        case SceneAction.NEW:
            currentScene.copy(newScene);
            return "cleared scene!";
        case SceneAction.REPLACE:
            // traverse the scene and find a node with name targetId. Replace it with the node of same name in newScene
            currentScene.traverse((node) => {
                if (node.name === targetId) {
                    currentScene.remove(node);
                }
            });
            currentScene.add(newScene);
            return "replaced scene";
        case SceneAction.ADD:
            currentScene.traverse((node) => {
                console.log(node);
                currentScene.add(node.clone())
            });
            // currentScene.add(newScene);
            return "Added scene";
        default:
            return "Default";
    }
};
