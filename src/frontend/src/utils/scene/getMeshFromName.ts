import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";
import {sceneRef} from "../../state/refs";

export function getMeshFromName(meshName: string) {
    const scene = sceneRef.current;
    if (!scene) {
        console.error("Scene is not set in model store");
        return null;
    }
    const mesh = scene.getObjectByName(meshName) as CustomBatchedMesh;
    if (mesh) {
        return mesh;
    }
    else {
        console.error(`Could not find mesh with name ${meshName}`);
        return null;
    }
}