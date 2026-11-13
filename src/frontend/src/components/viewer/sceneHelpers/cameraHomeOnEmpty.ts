// An emptied scene returns the camera to its starting pose, on whichever viewport is on screen (the
// three.js one or the three.js-free one): `ViewportApi.resetCamera`, the pose and the clip range the
// viewer starts with. Subscribed to the model state rather than hooked into the unload handlers
// because there are several ways to unload -- the Scene panel's x, a plugin's
// `ctx.scene.unloadModel`, Clear / Remove all -- and this is the one place that sees all of them.
// Only the transition INTO empty resets, so a user who framed a detail and unloaded nothing keeps
// their view. A replace load empties the scene on its way to the next model (a compile showing its
// result does, on every Shift+Enter): that waits for the replace to end and resets only if the scene
// is still empty then (state/sceneReplace), so the user's camera stays where it was.
//
// Why at all: fitting returns early on an empty scene, so unloading the last model left the camera
// wherever it was dragged, with a clip range sized for the model that went.
//
// It lives on the panels' side because the model state imports three.js, which the three.js-free
// viewport's own chunk must not.

import {useModelState} from "@/state/modelState";
import {onSceneReplaceEnd, sceneReplacing} from "@/state/sceneReplace";
import {activeViewport} from "@/viewport/api/viewportApi";

/** Reset the camera of the viewport on screen whenever the scene becomes empty; returns the unsubscribe. */
export function watchCameraHomeOnEmpty(): () => void {
    const empty = () => useModelState.getState().loadedSourceNames.size === 0;
    let wasLoaded = !empty();
    let emptiedByReplace = false;
    const stopModel = useModelState.subscribe((state) => {
        const isLoaded = state.loadedSourceNames.size > 0;
        if (wasLoaded && !isLoaded) {
            if (sceneReplacing()) emptiedByReplace = true;
            else activeViewport()?.resetCamera();
        }
        wasLoaded = isLoaded;
    });
    const stopReplace = onSceneReplaceEnd(() => {
        // A replace that brought nothing in (it failed) left the scene empty after all.
        if (emptiedByReplace && empty()) activeViewport()?.resetCamera();
        emptiedByReplace = false;
    });
    return () => {
        stopModel();
        stopReplace();
    };
}

let installed = false;

/** Install the rule; once per page. */
export function installCameraHomeOnEmpty(): void {
    if (installed) return;
    installed = true;
    watchCameraHomeOnEmpty();
}
