import {useModelState} from "@/state/modelState";
import {useAnimationStore} from "@/state/animationStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {animationControllerRef, modelKeyMapRef, sceneRef} from "@/state/refs";

// Tear the currently-loaded model out of the scene without loading a
// replacement. Mirrors what `replace_model` does up to the point of
// adding new content, then stops — leaving an empty scene + null state
// so the storage browser's "loaded" marker resets and the user can
// pick a different model.
//
// Note: we do NOT detach the root scene from r3f's tree (no
// `removeFromParent` here). The replace path uses that as a forced
// re-mount trick before adding new content; for a plain clear it just
// hides the canvas until something else loads.
export async function clear_loaded_model(): Promise<void> {
    const animationStore = useAnimationStore.getState();
    animationStore.setHasAnimation(false);
    animationStore.setIsPlaying(false);
    animationStore.setSelectedAnimation("No Animation");
    animationControllerRef.current?.clear();

    useTreeViewStore.getState().clearTreeData();
    useModelState.getState().translation = null;

    const three_scene = sceneRef.current;
    if (modelKeyMapRef.current) {
        for (const [, group] of modelKeyMapRef.current) {
            group.clear();
            three_scene?.remove(group);
        }
        modelKeyMapRef.current.clear();
    }

    const ms = useModelState.getState();
    ms.setModelUrl(null, null);
    // Drop both the single-name highlight and every overlay
    // entry, so the StorageBrowser checkboxes all uncheck.
    ms.clearLoadedSources();
    // Drop selection state too — without this the
    // useSelectedObjectStore map keeps references to the meshes we
    // just removed from the scene. Subsequent reloads (Show all
    // after Hide all, or a different file picked) end up with the
    // old refs as orphans: the count says "N selected" but no
    // CustomBatchedMesh in the live scene shows the highlight,
    // and any handler that walks selectedObjects (clipboard copy,
    // selection re-paint) operates on dead instances.
    useSelectedObjectStore.getState().clearSelectedObjects();
}
