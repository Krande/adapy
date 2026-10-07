// Time-history (transient) results: the steps of the field are TIME FRAMES of one
// run -- explicit dynamics animation states -- not load steps or mode shapes.
//
// Two things follow, and both differ from static / eigen fields:
//
//  * The first frame is the undeformed state at t = 0: zero displacement, zero
//    stress. Opening there shows an undeformed, uniformly blue model, so a
//    transient result opens on its LAST frame instead.
//  * "Play" means playing the history: advance through the frames at true
//    scale. Sweeping the deformation factor of one frame (what play does for a
//    static step or a mode shape) shows nothing at t = 0 and the wrong thing
//    anywhere else.

import type {FeaManifestField} from "@/services/viewerApi";

/** Frames per second the playback aims for. Each frame is a ranged blob fetch, so
 * playback runs as fast as the network allows up to this rate, never faster. */
export const TIME_HISTORY_FPS = 12;

export function isTimeHistory(field: Pick<FeaManifestField, "analysis_kind"> | null | undefined): boolean {
    return field?.analysis_kind === "transient";
}

/** The step a freshly loaded field opens on: the last frame of a time history, else 0. */
export function initialStepIndex(field: Pick<FeaManifestField, "analysis_kind" | "n_steps"> | null): number {
    if (!field || !isTimeHistory(field)) return 0;
    return Math.max(0, field.n_steps - 1);
}

export interface TimeHistoryClock {
    /** Seconds accumulated towards the next frame. */
    elapsed: number;
    /** A frame's fetch is still running; don't queue another behind it. */
    inFlight: boolean;
}

/**
 * Advance the playback clock. Returns the step to show next, or null when it is not
 * time yet (or the previous frame is still loading). Wraps from the last frame to the
 * first, so play loops the history. Pure: the caller applies the step.
 */
export function nextTimeHistoryStep(
    clock: TimeHistoryClock,
    deltaSeconds: number,
    stepIndex: number,
    nSteps: number,
    fps: number = TIME_HISTORY_FPS,
): number | null {
    if (nSteps <= 1 || fps <= 0) return null;
    clock.elapsed += deltaSeconds;
    if (clock.inFlight || clock.elapsed < 1 / fps) return null;
    clock.elapsed = 0;
    return (stepIndex + 1) % nSteps;
}

/** Decimals that resolve the spacing between frames: 1.5 ms apart -> 3 decimals. */
export function timeDecimals(stepValues: number[]): number {
    let minDelta = Infinity;
    for (let i = 1; i < stepValues.length; i++) {
        const d = Math.abs(stepValues[i] - stepValues[i - 1]);
        if (d > 0 && d < minDelta) minDelta = d;
    }
    if (!Number.isFinite(minDelta)) return 3;
    return Math.min(6, Math.max(0, Math.ceil(-Math.log10(minDelta))));
}

/** A frame time with a fixed number of decimals for the whole history, so the
 * readout neither jitters in width nor shows float noise (0.0600001 -> 0.060). */
export function formatStepTime(value: number, stepValues: number[]): string {
    return value.toFixed(timeDecimals(stepValues));
}
