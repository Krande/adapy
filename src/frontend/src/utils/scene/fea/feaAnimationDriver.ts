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

import {useFeaAnimationStore, type FeaAnimationState} from "@/state/feaAnimationStore";

let elapsed = 0;

/** Reset the phase. Called when the user presses stop, or when a
 * new session loads; without this the next play would resume
 * mid-sweep with a discontinuous jump. */
export function resetFeaAnimationPhase(): void {
    elapsed = 0;
}

/** The phase, for a host that runs several viewers off the one store and
 * has to park each one's phase while another is active (the paradoc embed). */
export function getFeaAnimationPhase(): number {
    return elapsed;
}

/** Restore a phase saved with ``getFeaAnimationPhase``. */
export function setFeaAnimationPhase(value: number): void {
    elapsed = value;
}

/** The fields of the FEA session the oscillator reads. */
export type FeaSweepState = Pick<
    FeaAnimationState,
    "isPlaying" | "sessionActive" | "mesh" | "period" | "range" | "scaleFactor"
>;

/**
 * One oscillator step for ``state``, starting from phase ``phase``: writes the
 * mesh's morph influence and returns the new phase and the swept factor, or
 * null when there is nothing to drive (not playing, no session, no mesh).
 *
 * Pure in the store: it touches only the mesh. ``tickFeaAnimation`` runs it on
 * the store's session; a host with several viewers runs it on the session of a
 * viewer that is not the active one, with that viewer's own phase.
 */
export function stepFeaSweep(
    state: FeaSweepState,
    phase: number,
    deltaSeconds: number,
): {phase: number; factor: number} | null {
    if (!state.isPlaying || !state.sessionActive || !state.mesh) {
        return null;
    }
    const period = state.period;
    if (period <= 0) return null;

    const next = phase + deltaSeconds;
    const t = (next % period) / period; // 0..1

    // sin sweep over [low, high]: map 0..1 → -1..1 → low..high.
    const sin = Math.sin(t * 2 * Math.PI);
    const [lo, hi] = state.range;
    const mid = (lo + hi) / 2;
    const half = (hi - lo) / 2;
    const factor = mid + half * sin;

    // Drive the mesh directly — bypassing the store keeps the RAF
    // path GPU-only on the hot path.
    //
    // ``scaleFactor`` exaggerates the morph delta on top of the
    // [-1..1] / [0..1] sweep range without touching the slider's
    // visible value. Default 1 leaves the behaviour identical to
    // before this knob landed.
    if (state.mesh.morphTargetInfluences) {
        state.mesh.morphTargetInfluences[0] = factor * state.scaleFactor;
    }
    return {phase: next, factor};
}

/** Advance the deformation factor by ``deltaSeconds``. Cheap to
 * call when not playing — early-returns. The store update only
 * fires when the factor actually changes (to avoid waking React
 * subscribers every frame). */
export function tickFeaAnimation(deltaSeconds: number): void {
    const state = useFeaAnimationStore.getState();
    const stepped = stepFeaSweep(state, elapsed, deltaSeconds);
    if (stepped === null) return;
    elapsed = stepped.phase;
    const {factor} = stepped;

    // The store still gets the current value so the UI slider follows
    // the sweep. Throttle store updates: only push a new value when the
    // slider would visibly change. ~120 steps over the full range is
    // below the slider's render granularity but well under React's
    // commit cost.
    const [lo, hi] = state.range;
    const lastFactor = state.factor;
    if (Math.abs(factor - lastFactor) > (hi - lo) / 240) {
        state.setFactor(factor);
    }
}
