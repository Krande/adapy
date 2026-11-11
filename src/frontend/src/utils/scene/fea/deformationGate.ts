// Whether the FEA model is drawn deformed: a plugin (a UI shell's modes) can ask for the
// undeformed shape without touching the user's warp setting.
//
// Core writes the warp influence from about ten places (the playback driver, field and legend
// loads, the scale slider, animation export), so the gate is not one more writer racing them: it
// runs once per frame, after the playback tick and before the viewport draws, and has the last
// word. While no owner holds it, it does nothing. When the last owner lets go, it puts back the
// influence the user's settings ask for, which nothing else would rewrite while playback is paused.
//
// Who holds it is kept here, for both viewports. Where the influence goes is the viewport's: the
// three.js loader's meshes (./threeDeformationGate.ts), or the layers of a result drawn through
// result layers. No three.js in this module.

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {requestRender} from "@/state/perfStore";

const undeformedFor = new Set<string>();
let applied = false;

/** A result drawn through result layers alone: its layers take the influence. */
function layerResult() {
    const s = useFeaAnimationStore.getState();
    return s.mesh === null ? s.resultLayer : null;
}

function writeToLayers(influence: number): void {
    layerResult()?.setInfluence(influence);
}

/** `shown: false` holds the FEA model at its undeformed shape until this owner says `true` again
 *  (or is released). Several owners can hold it; the model is undeformed while any does. */
export function setFeaDeformationShown(owner: string, shown: boolean): void {
    const changed = shown ? undeformedFor.delete(owner) : !undeformedFor.has(owner);
    if (!shown) undeformedFor.add(owner);
    if (!changed) return;
    requestRender();
    // A viewport that draws only when something changed has no frame to wait for: writing the
    // layers is what asks it for one.
    if (layerResult()) applyFeaDeformationGate();
}

/** Drop whatever `owner` asked for (its plugin's overlays are being removed). */
export function releaseFeaDeformation(owner: string): void {
    setFeaDeformationShown(owner, true);
}

/** False while any owner holds the model undeformed. */
export function feaDeformationShown(): boolean {
    return undeformedFor.size === 0;
}

/** Called by a viewport's frame loop each frame, before drawing. `write` puts an influence on what
 *  that viewport draws the result with; left out, the layers of the shown result take it. */
export function applyFeaDeformationGate(write: (influence: number) => void = writeToLayers): void {
    const hold = undeformedFor.size > 0;
    if (!hold && !applied) return;
    // Letting go: the influence the user's settings ask for. With the warp toggled off core installs
    // an all-zero displacement, so this moves nothing then; while playing, the driver rewrites it next tick.
    const s = useFeaAnimationStore.getState();
    const influence = hold ? 0 : s.factor * s.scaleFactor;
    if (Number.isFinite(influence)) write(influence);
    applied = hold;
}

/** Tests only. */
export function _resetFeaDeformationGate(): void {
    undeformedFor.clear();
    applied = false;
}
