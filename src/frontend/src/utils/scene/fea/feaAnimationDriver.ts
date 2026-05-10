// RAF-driven oscillator for the streaming-FEA deformation factor.
//
// Lives outside the THREE.AnimationMixer pipeline by design: that
// pipeline binds to GLTF clips, and the user flagged it as fragile.
// Here we just sweep ``mesh.morphTargetInfluences[0]`` through the
// active range each frame while the picker is in "play" mode.
//
// The render loop in ThreeCanvas.tsx calls ``tickFeaAnimation``
// once per frame; nothing else needs to know about this module.
//
// Sweep is sin-shaped so the visible motion eases in/out at the
// extremes — feels closer to a "natural" mode shape than a sawtooth.

import {useFeaAnimationStore} from "@/state/feaAnimationStore";

let elapsed = 0;

/** Reset the phase. Called when the user presses stop, or when a
 * new session loads; without this the next play would resume
 * mid-sweep with a discontinuous jump. */
export function resetFeaAnimationPhase(): void {
    elapsed = 0;
}

/** Advance the deformation factor by ``deltaSeconds``. Cheap to
 * call when not playing — early-returns. The store update only
 * fires when the factor actually changes (to avoid waking React
 * subscribers every frame). */
export function tickFeaAnimation(deltaSeconds: number): void {
    const state = useFeaAnimationStore.getState();
    if (!state.isPlaying || !state.sessionActive || !state.mesh) {
        return;
    }
    const period = state.period;
    if (period <= 0) return;

    elapsed += deltaSeconds;
    const phase = (elapsed % period) / period; // 0..1

    // sin sweep over [low, high]: map 0..1 → -1..1 → low..high.
    const sin = Math.sin(phase * 2 * Math.PI);
    const [lo, hi] = state.range;
    const mid = (lo + hi) / 2;
    const half = (hi - lo) / 2;
    const factor = mid + half * sin;

    // Drive the mesh directly — bypassing the store keeps the RAF
    // path GPU-only on the hot path. The store still gets the
    // current value so the UI slider follows the sweep.
    if (state.mesh.morphTargetInfluences) {
        state.mesh.morphTargetInfluences[0] = factor;
    }

    // Throttle store updates: only push a new value when the slider
    // would visibly change. ~120 steps over the full range is below
    // the slider's render granularity but well under React's
    // commit cost.
    const lastFactor = state.factor;
    if (Math.abs(factor - lastFactor) > (hi - lo) / 240) {
        state.setFactor(factor);
    }
}
