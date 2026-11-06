// The result transport's actions (play, pause, stop, step, deformation scale) as
// functions of the FEA animation store. The Simulation panel's transport bar and
// core's playback toolbar both call these, so the two behave the same.

import {useFeaAnimationStore} from "@/state/feaAnimationStore";

import {resetFeaAnimationPhase} from "./feaAnimationDriver";

const store = () => useFeaAnimationStore.getState();

/** Is there a result to play: a streaming session with a step to show. */
export function playbackAvailable(): boolean {
    const s = store();
    return s.sessionActive && s.nSteps > 0;
}

/** Start playing. A time history first loads its frames in the background so play stops waiting on the network. */
export function playResults(): void {
    const s = store();
    s.setIsPlaying(true);
    if (s.timeHistory) s.startFramePreload?.();
}

/** Pause; a time history repaints the shown frame the ordinary way (picking, probe and legend describe it). */
export function pauseResults(): void {
    const s = store();
    s.setIsPlaying(false);
    if (s.timeHistory) void s.applyStep?.(s.stepIndex);
}

export function togglePlayResults(): void {
    if (store().isPlaying) pauseResults();
    else playResults();
}

/** Show step `index` (clamped to the field's steps). */
export function setResultStep(index: number): void {
    const s = store();
    const next = Math.max(0, Math.min(Math.max(0, s.nSteps - 1), Math.round(index)));
    s.setStepIndex(next);
    if (s.applyStep) void s.applyStep(next);
}

/** Step forward or back by `delta`, wrapping round the ends. */
export function stepResults(delta: number): void {
    const {stepIndex, nSteps} = store();
    if (nSteps <= 0) return;
    setResultStep((((stepIndex + delta) % nSteps) + nSteps) % nSteps);
}

/** Stop: a time history goes back to its first frame; anything else back to the undeformed shape. */
export function stopResults(): void {
    const s = store();
    s.setIsPlaying(false);
    resetFeaAnimationPhase();
    if (s.timeHistory) {
        setResultStep(0);
        return;
    }
    s.setFactor(0);
    if (s.mesh?.morphTargetInfluences) s.mesh.morphTargetInfluences[0] = 0;
}

/** Set the deformation scale (the user's own, so the loader stops choosing one) and apply it to the shown shape. */
export function setResultScale(raw: number): void {
    if (!isFinite(raw)) return;
    const s = store();
    s.setScaleFactor(raw);
    if (s.mesh?.morphTargetInfluences) s.mesh.morphTargetInfluences[0] = s.factor * raw;
}

/** Set the paused deformation factor within the field's range and apply it. */
export function setResultFactor(factor: number): void {
    const s = store();
    s.setFactor(factor);
    if (s.mesh?.morphTargetInfluences) s.mesh.morphTargetInfluences[0] = factor * s.scaleFactor;
}
