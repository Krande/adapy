import * as THREE from 'three';
import {useModelStore} from "../../state/modelStore";

export function getMeshFromName(meshName: string) {
    const scene = useModelStore.getState().scene;
    if (!scene) {
        console.error("Scene is not set in model store");
        return null;
    }
    const mesh = scene.getObjectByName(meshName) as THREE.Mesh;
    if (mesh) {
        return mesh;
    }
    else {
        console.error(`Could not find mesh with name ${meshName}`);
        return null;
    }
}