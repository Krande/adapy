import {useModelState} from "@/state/modelState";
import {useAnimationStore} from "@/state/animationStore";
import {useTreeViewStore} from "@/state/treeViewStore";
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
    ms.setLoadedSourceName(null);
}
