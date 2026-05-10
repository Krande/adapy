import {Message} from "@/flatbuffers/wsock/message";
import {useModelState} from "@/state/modelState";
import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {add_mesh_to_scene} from "./append_to_scene_from_message";

import {ungzip} from 'pako';
import {SetupModelPrepareHook, setupModelLoaderAsync} from "@/components/viewer/sceneHelpers/setupModelLoader";
import {animationControllerRef, modelKeyMapRef, sceneRef} from "@/state/refs";
import {useTreeViewStore} from "@/state/treeViewStore";
import {loadGLTFfrombase64} from "../loadGLTFfrombase64";
import {useAnimationStore} from "@/state/animationStore";
import {runtime} from "@/runtime/config";


export function load_base64_model(){
    console.log("B64GLTF exists, loading model");
    const b64 = runtime.b64Gltf();
    if (!b64) return;
    const blob_uri = loadGLTFfrombase64(b64);
    useModelState.getState().setModelUrl(blob_uri, SceneOperations.REPLACE);

}

export async function replace_model(url: string, prepareHook?: SetupModelPrepareHook) {
        // Clear animation state first
    const animationStore = useAnimationStore.getState();
    animationStore.setHasAnimation(false);
    animationStore.setIsPlaying(false);
    animationStore.setSelectedAnimation("No Animation");

    // Clear animation controller
    animationControllerRef.current?.clear();

    useModelState.getState().translation = null;
    useTreeViewStore.getState().clearTreeData(); // Clear the tree view
    // Drop any per-source group refs from prior overlays — the
    // scene is about to be wiped, so those refs are no longer
    // valid. clearLoadedSources also empties loadedSourceNames so
    // every StorageBrowser checkbox unchecks together.
    useModelState.getState().clearLoadedSources();

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
    return await setupModelLoaderAsync(url, false, prepareHook);
}

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
    if (operation == SceneOperations.REPLACE) {
        const group = await replace_model(url);
        // Register this load under the source name so the
        // StorageBrowser checkbox stays in sync and a future
        // unload (uncheck) can find the right group to remove.
        const sourceName = fileObject.name();
        if (group && sourceName) {
            useModelState.getState().registerLoadedSource(sourceName, group);
        }
    } else if (operation == SceneOperations.REMOVE) {
        console.error("Currently unsupported operation", operation);
    } else if (operation == SceneOperations.ADD) {
        let mesh = message.package_()?.mesh()?.unpack();
        if (mesh) {
            await add_mesh_to_scene(mesh)
        } else {
            const group = await setupModelLoaderAsync(url, true);
            const sourceName = fileObject.name();
            if (group && sourceName) {
                useModelState.getState().registerLoadedSource(sourceName, group);
            }
        }
    } else {
        console.error("Unknown operation type: ", operation);
    }
}
