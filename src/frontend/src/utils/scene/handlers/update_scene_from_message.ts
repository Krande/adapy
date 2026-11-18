import {Message} from "@/flatbuffers/wsock/message";
import {useModelState} from "@/state/modelState";
import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {add_mesh_to_scene} from "./append_to_scene_from_message";

import {gunzipToBlob} from '@/utils/gunzipToBlob';
import {SetupModelPrepareHook, setupModelLoaderAsync, type SetupModelLoaderOptions} from "@/components/viewer/sceneHelpers/setupModelLoader";
import {loadModel} from "@/components/viewer/sceneHelpers/loadModel";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {loadGLTFfrombase64} from "../loadGLTFfrombase64";
import {runtime} from "@/runtime/config";
import {duringSceneReplace} from "@/state/sceneReplace";
import {clearScene} from "./clear_scene";


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
    /** What the model is called to a person: its root row in the Scene tree. */
    displayName?: string;
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

/** Tear the scene down and load `options.url` as its one model; the scene's emptying meanwhile is
 *  part of the replace (state/sceneReplace), so the camera is left where the user has it. */
export function replace_model(options: ReplaceModelOptions) {
    return duringSceneReplace(() => replaceModelNow(options));
}

async function replaceModelNow(options: ReplaceModelOptions) {
    const {
        url,
        prepareHook,
        sourceName,
        displayName,
        translate = false,
        requestHeaders,
        metrics,
        autoFitOverride,
    } = options;
    // The scene's one teardown, as every clear takes it (./clear_scene): this is a replace, so what
    // the load below sets itself is left to it, and the queue and an open procedural model stay.
    await clearScene({reason: "replace", replacing: true, keepQueuedLoads: true, keepProcedural: true});

    const three_scene = getViewerRuntime().scene.current;
    if (!three_scene) {
        console.warn("No scene found");
        return;
    }
    // Detached for the load below to mount it again (its forced re-mount).
    three_scene.removeFromParent();
    return await setupModelLoaderAsync({
        modelUrl: url,
        translate,
        prepareHook,
        sourceName,
        displayName,
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

    // A large model arrives gzipped: inflated by the browser itself, off the main thread.
    const type = 'model/gltf-binary';
    const blob = fileObject.compressed() ? await gunzipToBlob(data, type) : new Blob([data as BlobPart], {type});
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
