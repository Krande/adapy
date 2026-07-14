import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {runtime} from "@/runtime/config";
import {viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {replace_model} from "./update_scene_from_message";

/**
 * Render a FEA result file with a specific (step, field) pick.
 *
 * Different from view_file_object_from_server in two ways: the convert
 * call carries the (step, field) pick so the worker tessellates with
 * those values, and we fetch the picked-derived GLB blob over HTTP and
 * load it into the scene directly. The flatbuffer VIEW_FILE_OBJECT
 * path can't represent a non-default derived key today.
 *
 * Throws on convert / fetch errors so the caller can surface them.
 */
export async function view_picked_fea_render(
    sourceName: string,
    step: number,
    field: string,
): Promise<void> {
    if (!runtime.isRestMode()) {
        throw new Error("FEA result picks are only available in REST mode");
    }
    if (!runtime.convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }
    const {ensureConverted} = await import("@/services/conversion");

    const scope = scopeUrlPart(useScopeStore.getState().current);
    const derivedKey = await ensureConverted(scope, sourceName, "glb", {step, field});

    const buf = await viewerApi.getBlob(scope, derivedKey);
    const blob = new Blob([buf], {type: "model/gltf-binary"});
    const url = URL.createObjectURL(blob);
    try {
        const group = await replace_model(url);
        const ms = useModelState.getState();
        ms.setModelUrl(url, SceneOperations.REPLACE);
        ms.setLoadedSourceName(sourceName);
        // Register the loaded group AFTER setLoadedSourceName (which clears
        // loadedSourceGroups) so the FEA result mesh gets a working visibility
        // toggle in the loaded-models list instead of a static "streaming" eye.
        if (group && sourceName) {
            ms.registerLoadedSource(sourceName, group);
        }
    } catch (err) {
        URL.revokeObjectURL(url);
        throw err;
    }
}
