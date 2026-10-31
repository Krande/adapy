// Whether the FEA model is drawn deformed: a plugin (a UI shell's modes) can ask for the
// undeformed shape without touching the user's warp setting.
//
// Core writes the warp influence from about ten places (the playback driver, field and legend
// loads, the scale slider, animation export), so the gate is not one more writer racing them: it
// runs once per frame, after the playback tick and before either renderer draws, and has the last
// word. While no owner holds it, it does nothing. When the last owner lets go, it puts back the
// influence the user's settings ask for, which nothing else would rewrite while playback is paused.

import * as THREE from "three";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {requestRender} from "@/state/perfStore";
import {feaSession} from "./streaming/session";

const undeformedFor = new Set<string>();
let applied = false;

/** `shown: false` holds the FEA model at its undeformed shape until this owner says `true` again
 *  (or is released). Several owners can hold it; the model is undeformed while any does. */
export function setFeaDeformationShown(owner: string, shown: boolean): void {
    const changed = shown ? undeformedFor.delete(owner) : !undeformedFor.has(owner);
    if (!shown) undeformedFor.add(owner);
    if (changed) requestRender();
}

/** Drop whatever `owner` asked for (its plugin's overlays are being removed). */
export function releaseFeaDeformation(owner: string): void {
    setFeaDeformationShown(owner, true);
}

/** False while any owner holds the model undeformed. */
export function feaDeformationShown(): boolean {
    return undeformedFor.size === 0;
}

function surfaces(): THREE.Mesh[] {
    const active = feaSession.active;
    const out: THREE.Mesh[] = [];
    if (active?.mesh) out.push(active.mesh as THREE.Mesh);
    if (active?.beamSolidMesh) out.push(active.beamSolidMesh as THREE.Mesh);
    return out;
}

/** Called by the render loop each frame, before drawing. */
export function applyFeaDeformationGate(): void {
    const hold = undeformedFor.size > 0;
    if (!hold && !applied) return;
    // Letting go: the influence the user's settings ask for. With the warp toggled off core installs
    // an all-zero displacement, so this moves nothing then; while playing, the driver rewrites it next tick.
    const s = useFeaAnimationStore.getState();
    const influence = hold ? 0 : s.factor * s.scaleFactor;
    for (const mesh of surfaces()) {
        const inf = mesh.morphTargetInfluences;
        if (inf && inf.length > 0 && Number.isFinite(influence)) inf[0] = influence;
    }
    applied = hold;
}

/** Tests only. */
export function _resetFeaDeformationGate(): void {
    undeformedFor.clear();
    applied = false;
}
