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

import {nextTimeHistoryStep, type TimeHistoryClock} from "./timeHistory";

let elapsed = 0;
const historyClock: TimeHistoryClock = {elapsed: 0, inFlight: false};

/** Reset the phase. Called when the user presses stop, or when a
 * new session loads; without this the next play would resume
 * mid-sweep with a discontinuous jump. */
export function resetFeaAnimationPhase(): void {
    elapsed = 0;
    historyClock.elapsed = 0;
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
    if (state.timeHistory) {
        tickTimeHistory(deltaSeconds);
        return;
    }
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

/** Play for a time-history field: step through the frames at true scale (factor 1),
 * one frame fetch at a time, looping. Same path as dragging the step slider.
 *
 * A step may be a load combination computed on request (fea/caseSteps.ts): the
 * next frame is not asked for until ``applyStep`` -- which waits for the case to
 * be materialised -- has settled (``inFlight``), so cases materialise in
 * sequence; ``applyStep`` itself warms up the following case while it does. */
function tickTimeHistory(deltaSeconds: number): void {
    const state = useFeaAnimationStore.getState();
    if (!state.isPlaying || !state.sessionActive || !state.mesh || !state.applyStep) return;
    const next = nextTimeHistoryStep(historyClock, deltaSeconds, state.stepIndex, state.nSteps);
    if (next === null) return;
    const [, hi] = state.range;
    if (state.factor !== hi) state.setFactor(hi);
    state.setStepIndex(next);
    historyClock.inFlight = true;
    const started = performance.now();
    state
        .applyStep(next, {playback: true})
        .then(() => recordPlaybackFrame(next, started))
        .catch((err) => console.warn("[fea] time-history frame failed", err))
        .finally(() => {
            historyClock.inFlight = false;
        });
}

// ── playback timing ─────────────────────────────────────────────────────────
//
// How long each played frame took, so a stutter report comes with numbers:
//   * paint  -- request to painted: fetch (when not cached) + the CPU colouring kernel;
//   * shown  -- request to the frame after the next render, which adds what the
//               renderer does with the paint (the morph texture rebuild, the uploads).
// A `fea:frame` entry per frame in the DevTools performance timeline, a one-line
// summary in the console once per pass through the history, and the rolling figures
// on `getFeaPlaybackStats()` (also `window.__feaPlayback()`).

const PLAYBACK_WINDOW = 120;
const paintMs: number[] = [];
const shownMs: number[] = [];
let framesSinceReport = 0;

export interface FeaLatency {
    meanMs: number;
    p95Ms: number;
    maxMs: number;
}

export interface FeaPlaybackStats {
    frames: number;
    paint: FeaLatency;
    shown: FeaLatency;
}

function latency(samples: number[]): FeaLatency {
    const sorted = [...samples].sort((a, b) => a - b);
    const mean = samples.reduce((a, b) => a + b, 0) / Math.max(1, samples.length);
    return {
        meanMs: round1(mean),
        p95Ms: round1(sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] ?? 0),
        maxMs: round1(sorted[sorted.length - 1] ?? 0),
    };
}

/** Rolling frame latencies over the last frames played, or null before any. */
export function getFeaPlaybackStats(): FeaPlaybackStats | null {
    if (paintMs.length === 0) return null;
    return {frames: paintMs.length, paint: latency(paintMs), shown: latency(shownMs.length ? shownMs : paintMs)};
}

function push(samples: number[], ms: number): void {
    samples.push(ms);
    if (samples.length > PLAYBACK_WINDOW) samples.shift();
}

function recordPlaybackFrame(step: number, started: number): void {
    const painted = performance.now() - started;
    push(paintMs, painted);
    const finish = () => {
        const shown = performance.now() - started;
        push(shownMs, shown);
        try {
            performance.measure("fea:frame", {start: started, duration: shown, detail: {step, paintMs: painted}});
        } catch {
            // An old browser without the options form: the console summary still runs.
        }
    };
    // The first callback runs before the render that draws this paint, the second
    // after it.
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => requestAnimationFrame(finish));
    else finish();
    const nSteps = useFeaAnimationStore.getState().nSteps;
    if (++framesSinceReport >= Math.max(1, nSteps)) {
        framesSinceReport = 0;
        const stats = getFeaPlaybackStats();
        // eslint-disable-next-line no-console
        if (stats) console.info("[fea] playback frame ms", stats);
    }
}

function round1(x: number): number {
    return Math.round(x * 10) / 10;
}

if (typeof window !== "undefined") {
    (window as unknown as {__feaPlayback?: () => FeaPlaybackStats | null}).__feaPlayback = getFeaPlaybackStats;
}
