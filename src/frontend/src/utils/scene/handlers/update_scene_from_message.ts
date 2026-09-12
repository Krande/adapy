import {Message} from "@/flatbuffers/wsock/message";
import {useModelState} from "@/state/modelState";
import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {add_mesh_to_scene} from "./append_to_scene_from_message";

import {ungzip} from 'pako';
import {SetupModelPrepareHook, setupModelLoaderAsync, type SetupModelLoaderOptions} from "@/components/viewer/sceneHelpers/setupModelLoader";
import {loadModel} from "@/components/viewer/sceneHelpers/loadModel";
import {clearActiveFeaStreaming} from "./load_fea_streaming";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {useTreeViewStore} from "@/state/treeViewStore";
import {loadGLTFfrombase64} from "../loadGLTFfrombase64";
import {disposeObject3D} from "@/utils/scene/dispose_object";
import {useAnimationStore} from "@/state/animationStore";
import {runtime} from "@/runtime/config";


export function load_base64_model(){
    console.log("B64GLTF exists, loading model");
    const b64 = runtime.b64Gltf();
    if (!b64) return;
    const blob_uri = loadGLTFfrombase64(b64);
    useModelState.getState().setModelUrl(blob_uri, SceneOperations.REPLACE);

}

/** Options for {@link replace_model}. Mirrors {@link SetupModelLoaderOptions}
 * minus ``sourceUpAxis`` (the scene-replacing paths all feed Z-up content) and
 * with ``translate`` defaulting to false rather than true. */
export interface ReplaceModelOptions {
    url: string;
    prepareHook?: SetupModelPrepareHook;
    sourceName?: string;
    /** Recenter the model to the scene origin from its bbox (like the CAD loader). FEA/FEM
     * meshes can sit far from origin (real instance coordinates), so the streaming load passes
     * true; the legacy WS-message caller keeps the old no-recenter behaviour. */
    translate?: boolean;
    /** Auth headers when ``url`` is an authed REST streaming GET (REST-mode view-by-URL). */
    requestHeaders?: Record<string, string>;
    /** Optional admin load-metrics recorder (REST view path). No-op when absent. */
    metrics?: import("@/utils/scene/loadMetrics").LoadMetricsRecorder | null;
    /** Override the post-load auto-fit (undefined = scene config; false = never). */
    autoFitOverride?: boolean;
}

export async function replace_model(options: ReplaceModelOptions) {
    const {
        url,
        prepareHook,
        sourceName,
        translate = false,
        requestHeaders,
        metrics,
        autoFitOverride,
    } = options;
        // Clear animation state first
    const animationStore = useAnimationStore.getState();
    animationStore.setHasAnimation(false);
    animationStore.setIsPlaying(false);
    animationStore.setSelectedAnimation("No Animation");

    // Clear animation controller
    getViewerRuntime().animationController.current?.clear();

    useModelState.getState().translation = null;
    useTreeViewStore.getState().clearTreeData(); // Clear the tree view
    // Drop any per-source group refs from prior overlays — the
    // scene is about to be wiped, so those refs are no longer
    // valid. clearLoadedSources also empties loadedSourceNames so
    // every StorageBrowser checkbox unchecks together.
    useModelState.getState().clearLoadedSources();
    // FEA streaming session is tied to the mesh that's about to be
    // ripped out — clear it before the new scene comes in so the
    // SimulationControls UI flips back from FEA-mode to the GLTF
    // clip path.
    clearActiveFeaStreaming();

    const three_scene = getViewerRuntime().scene.current;
    if (!three_scene) {
        console.warn("No scene found");
        return;
    }
    // clear the current scene
    three_scene.removeFromParent();
    const modelKeyMap = getViewerRuntime().modelKeyMap.current;
    if (modelKeyMap) {
        for (let key of modelKeyMap.keys()) {
            let existing_group = modelKeyMap.get(key);
            if (existing_group) {
                // Free GPU buffers of the outgoing model before detaching — clear() alone
                // leaves geometry/material in the renderer caches (VRAM doesn't fall).
                disposeObject3D(existing_group);
                existing_group.clear();
            }

        }
    }
    return await setupModelLoaderAsync({
        modelUrl: url,
        translate,
        prepareHook,
        sourceName,
        requestHeaders,
        metrics,
        autoFitOverride,
    });
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
    const sourceName = fileObject.name();
    if (operation == SceneOperations.REPLACE) {
        // sourceName labels the tree root (GLB filename) and keeps the
        // StorageBrowser checkbox in sync (unload finds the right group).
        await loadModel({sourceName: sourceName ?? "", bytes: {from: "url", url}});
    } else if (operation == SceneOperations.REMOVE) {
        console.error("Currently unsupported operation", operation);
    } else if (operation == SceneOperations.ADD) {
        let mesh = message.package_()?.mesh()?.unpack();
        if (mesh) {
            await add_mesh_to_scene(mesh)
        } else {
            await loadModel({
                sourceName: sourceName ?? "",
                bytes: {from: "url", url},
                placement: "overlay",
            });
        }
    } else {
        console.error("Unknown operation type: ", operation);
    }
}
