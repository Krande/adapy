// An emptied scene returns the camera to its starting pose, on whichever viewport is on screen (the
// three.js one or the three.js-free one): `ViewportApi.resetCamera`, the pose and the clip range the
// viewer starts with. Subscribed to the model state rather than hooked into the unload handlers
// because there are several ways to unload -- the Scene panel's x, a plugin's
// `ctx.scene.unloadModel`, Clear / Remove all -- and this is the one place that sees all of them.
// Only the transition INTO empty resets, so a user who framed a detail and unloaded nothing keeps
// their view.
//
// Why at all: fitting returns early on an empty scene, so unloading the last model left the camera
// wherever it was dragged, with a clip range sized for the model that went.
//
// It lives on the panels' side because the model state imports three.js, which the three.js-free
// viewport's own chunk must not.

import {useModelState} from "@/state/modelState";
import {activeViewport} from "@/viewport/api/viewportApi";

/** Reset the camera of the viewport on screen whenever the scene becomes empty; returns the unsubscribe. */
export function watchCameraHomeOnEmpty(): () => void {
    let wasLoaded = useModelState.getState().loadedSourceNames.size > 0;
    return useModelState.subscribe((state) => {
        const isLoaded = state.loadedSourceNames.size > 0;
        if (wasLoaded && !isLoaded) activeViewport()?.resetCamera();
        wasLoaded = isLoaded;
    });
}

let installed = false;

/** Install the rule; once per page. */
export function installCameraHomeOnEmpty(): void {
    if (installed) return;
    installed = true;
    watchCameraHomeOnEmpty();
}
