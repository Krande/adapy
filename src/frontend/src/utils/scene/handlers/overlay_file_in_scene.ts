// Add a stored GLB to the current scene without replacing what's
// already there. Bypasses the VIEW_FILE_OBJECT flatbuffer roundtrip
// (which the server hard-codes to SceneOperations.REPLACE) by pulling
// the blob directly through the REST API and feeding it to
// setupModelLoaderAsync with translate=false, so the new model lands
// at its real coordinates and overlays the previous one in place.
//
// Selection-mesh implications: each loaded model gets its own
// CustomBatchedMesh + per-model selection overlay, so picking on one
// doesn't bleed into the other. Tree view (single-model state) keeps
// showing only the first model's hierarchy — the second model is
// visible but won't have tree entries. That's acceptable for the
// debug/diff use case (overlay STEP gold + XML output to see
// missing/displaced plates).

import {viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {setupModelLoaderAsync} from "@/components/viewer/sceneHelpers/setupModelLoader";
import {ensureConvertedGlb} from "@/services/conversion";
import {runtime} from "@/runtime/config";

function derivedKeyForGlb(sourceKey: string): string {
    // Mirrors the server-side derived_key_for(target='glb') convention.
    // GLB-already files have no derivation; everything else is
    // _derived/<sourceKey>.glb. Importantly the SOURCE-IS-GLB case
    // returns sourceKey itself.
    return sourceKey.toLowerCase().endsWith(".glb")
        ? sourceKey
        : `_derived/${sourceKey}.glb`;
}

export async function overlay_file_in_scene(sourceName: string): Promise<void> {
    if (!runtime.isRestMode()) {
        // Overlay path is REST-only — desktop mode opens external apps,
        // not a shared 3D scene we can stack into.
        console.warn("overlay_file_in_scene: not in REST mode; ignoring");
        return;
    }

    const scope = scopeUrlPart(useScopeStore.getState().current);

    const isGlb = sourceName.toLowerCase().endsWith(".glb");
    if (!isGlb) {
        if (!runtime.convertEnabled()) {
            console.warn("overlay_file_in_scene: non-GLB source but conversion disabled");
            return;
        }
        await ensureConvertedGlb(scope, sourceName);
    }
    const glbKey = derivedKeyForGlb(sourceName);
    const blob = await viewerApi.getBlob(scope, glbKey);
    const url = URL.createObjectURL(new Blob([blob], {type: "model/gltf-binary"}));

    // translate=false: don't recenter the new model — keep its real
    // coordinates so it overlays the existing scene at the correct
    // world position. The first model loaded sets the translation;
    // overlays inherit it.
    await setupModelLoaderAsync(url, false);

    // Track the latest overlay as "loaded" — modelState only knows
    // about a single name today, so the most-recently-added one wins
    // the highlight. Good enough for v1.
    useModelState.getState().setLoadedSourceName(sourceName);
}
