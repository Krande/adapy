import {Message} from "../../../flatbuffers/wsock/message";
import {useModelState} from "../../../state/modelState";
import {SceneOperations} from "../../../flatbuffers/scene/scene-operations";
import {add_mesh_to_scene} from "./append_to_scene_from_message";

import {ungzip} from 'pako';
import {setupModelLoaderAsync} from "../../../components/viewer/sceneHelpers/setupModelLoader";
import {modelKeyMapRef, sceneRef} from "../../../state/refs";
import {useTreeViewStore} from "../../../state/treeViewStore";
import {modelStore} from "../../../state/model_worker/modelStore";


export async function update_scene_from_message(message: Message) {
    console.log('Received scene update message from server');
    let scene = message.scene();

    if (!scene) {
        console.error("No scene object found in the message");
        return;
    }
    let operation = scene.operation();

    let fileObject = scene.currentFile();
    if (!fileObject) {
        console.error("No file object found in the message");
        return;
    }

    let data = fileObject.filedataArray();
    if (!data) {
        console.error("No filedata found in the file object");
        return;
    }

    let compressed = fileObject.compressed(); // New field you added
    let finalData: Uint8Array;

    if (compressed) {
        console.log('Decompressing received GLB data...');
        finalData = ungzip(data); // using pako.gunzip
    } else {
        finalData = data;
    }

    const blob = new Blob([finalData], {type: 'model/gltf-binary'});
    const url = URL.createObjectURL(blob);
    const model_state = useModelState.getState()
    if (operation == SceneOperations.REPLACE) {
        model_state.setModelUrl(url, operation); // Set the URL for the model
        model_state.translation = null;
        useTreeViewStore.getState().clearTreeData(); // Clear the tree view

        const three_scene = sceneRef.current;
        if (!three_scene) {
            console.warn("No scene found");
            return;
        }
        // clear the current scene
        three_scene.removeFromParent();
        if (modelKeyMapRef.current) {
            for (let key of modelKeyMapRef.current.keys()) {
                let existing_group = modelKeyMapRef.current.get(key);
                if (existing_group) {
                    existing_group.clear();
                }

            }
        }
        await setupModelLoaderAsync(url, false);
    } else if (operation == SceneOperations.REMOVE) {
        console.error("Currently unsupported operation", operation);
    } else if (operation == SceneOperations.ADD) {
        let mesh = message.package_()?.mesh()?.unpack();
        if (mesh) {
            await add_mesh_to_scene(mesh)
        } else {
            await setupModelLoaderAsync(url, true);
        }
    } else {
        console.error("Unknown operation type: ", operation);
    }
}
