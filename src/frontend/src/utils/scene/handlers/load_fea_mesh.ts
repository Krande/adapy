import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {runtime} from "@/runtime/config";
import {FeaManifest, viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {replace_model} from "./update_scene_from_message";

/**
 * Load the geometry-only mesh GLB from a baked FEA artefact tree.
 *
 * Phase 1 step 3 partial: gives the streaming-viewer picker a visual
 * response (undeformed mesh) when the user clicks Apply. The
 * deformation shader + colormap that consume the picked field /
 * step / component land in step 4; until then this is a "did the
 * bake produce something I can see" smoke renderer.
 *
 * The manifest's ``mesh.url`` is a filename relative to the bake's
 * per-source prefix; this function composes the full storage key
 * and feeds the GLB bytes into the existing replace_model pipeline.
 */
export async function load_fea_mesh_into_scene(
    sourceName: string,
    manifest: FeaManifest,
): Promise<void> {
    if (!runtime.isRestMode()) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }

    const scope = scopeUrlPart(useScopeStore.getState().current);
    const cleanSrc = sourceName.replace(/^\/+/, "");
    const meshKey = `_derived/${cleanSrc}.fea/${manifest.mesh.url}`;
    const buf = await viewerApi.getBlob(scope, meshKey);

    const blob = new Blob([buf], {type: "model/gltf-binary"});
    const url = URL.createObjectURL(blob);
    try {
        await replace_model(url);
        const ms = useModelState.getState();
        ms.setModelUrl(url, SceneOperations.REPLACE);
        ms.setLoadedSourceName(sourceName);
    } catch (err) {
        URL.revokeObjectURL(url);
        throw err;
    }
}
