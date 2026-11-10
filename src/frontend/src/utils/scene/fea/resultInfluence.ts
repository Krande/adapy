// The deformation scale of the shown result, written where the viewport on screen draws it from.
//
// A result is drawn at `base + influence * displacement`. The influence is the one number that
// changes every frame while a mode shape plays, so this is the whole per-frame cost of playback:
// no vertex data is touched. No three.js here (the mesh is only named as a type).

import type {FeaAnimationState} from "@/state/feaAnimationStore";

/** The fields that say where the shown result is drawn. */
export type ResultTarget = Pick<FeaAnimationState, "resultLayer" | "mesh">;

/** Is there a result on screen to scale. */
export function hasResultTarget(state: ResultTarget): boolean {
    return state.resultLayer !== null || state.mesh !== null;
}

/** Draw the shown result at `influence` times its displacement: through its layer, or, for a
 *  session registered by its mesh alone, on the mesh's own morph influence. */
export function setResultInfluence(state: ResultTarget, influence: number): void {
    if (state.resultLayer) state.resultLayer.setInfluence(influence);
    else if (state.mesh?.morphTargetInfluences) state.mesh.morphTargetInfluences[0] = influence;
}
