// The three.js scene's half of Clear: every loaded model group out of the scene, its GPU buffers freed.
// The three.js viewport's `clearModels`; `clearScene` also calls it whichever viewport is on
// screen, so groups the three.js scene kept from before a renderer switch go too (idempotent).

import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {disposeObject3D} from "@/utils/scene/dispose_object";
import {forgetThreeObjectFade} from "@/utils/scene/threeObjectFade";

/** What `userData` key marks an object added to the three.js scene outside the model groups that a
 *  clear takes all the same: a robot loaded from its URDF (utils/robots.ts). */
export const CLEARED_WITH_MODELS = "__clearedWithModels";

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
    // What was added beside the model groups and goes with them (a URDF robot): it stayed.
    for (const object of threeScene?.children?.filter((o) => o.userData?.[CLEARED_WITH_MODELS]) ?? []) {
        disposeObject3D(object);
        threeScene!.remove(object);
    }
    // What was faded went with its meshes (utils/scene/threeObjectFade.ts).
    forgetThreeObjectFade();
    // The on-demand render loop will not fire until the camera moves otherwise, leaving the
    // just-cleared models on screen as ghosts.
    requestRender();
}
