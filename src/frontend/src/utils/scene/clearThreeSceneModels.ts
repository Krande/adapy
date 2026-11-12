// The three.js scene's half of Clear: every loaded model group out of the scene, its GPU buffers freed.
// The three.js viewport's `clearModels`; `clear_loaded_model` also calls it whichever viewport is on
// screen, so groups the three.js scene kept from before a renderer switch go too (idempotent).

import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {disposeObject3D} from "@/utils/scene/dispose_object";

export function clearThreeSceneModels(): void {
    const threeScene = getViewerRuntime().scene.current;
    const modelKeyMap = getViewerRuntime().modelKeyMap.current;
    if (modelKeyMap) {
        for (const [, group] of modelKeyMap) {
            // Free GPU buffers before detaching -- group.clear()/remove() alone leaves the
            // geometry/material in the renderer's caches, so VRAM never falls.
            disposeObject3D(group);
            group.clear();
            threeScene?.remove(group);
        }
        modelKeyMap.clear();
    }
    // The on-demand render loop will not fire until the camera moves otherwise, leaving the
    // just-cleared models on screen as ghosts.
    requestRender();
}
