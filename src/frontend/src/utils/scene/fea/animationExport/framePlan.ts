// Which frames an animation export renders, and how fast they play back.
//
// Mirrors what Play shows on screen, frame for frame, but deterministically:
//
//  * a time history (``analysis_kind`` "transient") exports every step at true
//    scale -- the run as it happened;
//  * a static step or a mode shape exports one cycle of the deformation sweep at
//    the current step (the same sin sweep the RAF oscillator draws).
//
// Pure: the exporter applies each frame.

/** One frame: which step to show and the deformation factor (slider value). */
export interface ExportFrame {
    stepIndex: number;
    factor: number;
    /** Text for the burned-in time / case label. */
    label: string;
}

export interface FramePlan {
    frames: ExportFrame[];
    fps: number;
    /** True when each frame needs a step fetch (a time history); false when only the
     * deformation factor changes between frames. */
    stepsChange: boolean;
}

export interface FramePlanInput {
    timeHistory: boolean;
    nSteps: number;
    stepIndex: number;
    range: [number, number];
    period: number;
    stepLabels: string[];
}

/** Frames per second of a time-history export. */
export const TIME_HISTORY_EXPORT_FPS = 12;
/** Frames in one cycle of a static / modal sweep. */
export const SWEEP_FRAMES = 36;

export function planExportFrames(input: FramePlanInput): FramePlan {
    const [lo, hi] = input.range;
    if (input.timeHistory && input.nSteps > 1) {
        const frames = Array.from({length: input.nSteps}, (_, i) => ({
            stepIndex: i,
            factor: hi,
            label: input.stepLabels[i] ?? String(i),
        }));
        return {frames, fps: TIME_HISTORY_EXPORT_FPS, stepsChange: true};
    }
    const mid = (lo + hi) / 2;
    const half = (hi - lo) / 2;
    const label = input.stepLabels[input.stepIndex] ?? String(input.stepIndex);
    const frames = Array.from({length: SWEEP_FRAMES}, (_, k) => ({
        stepIndex: input.stepIndex,
        factor: mid + half * Math.sin((2 * Math.PI * k) / SWEEP_FRAMES),
        label,
    }));
    const period = input.period > 0 ? input.period : 2;
    return {frames, fps: SWEEP_FRAMES / period, stepsChange: false};
}

/** H.264 and friends need even frame sizes; round the requested size down to even. */
export function evenSize(width: number, height: number): [number, number] {
    return [Math.max(2, Math.floor(width / 2) * 2), Math.max(2, Math.floor(height / 2) * 2)];
}

/** File name for the export: the source's base name and the field. */
export function exportFileName(sourceName: string | null, fieldName: string | null, ext: "mp4" | "gif"): string {
    const base = (sourceName ?? "animation").split("/").pop()!.replace(/\.[^.]+$/, "") || "animation";
    const field = (fieldName ?? "").replace(/[^A-Za-z0-9_.-]+/g, "_");
    return `${base}${field ? `_${field}` : ""}.${ext}`;
}
