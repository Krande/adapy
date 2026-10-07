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
    /** Playback rate of a time-history export; defaults to TIME_HISTORY_EXPORT_FPS. */
    fps?: number;
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
        return {frames, fps: input.fps && input.fps > 0 ? input.fps : TIME_HISTORY_EXPORT_FPS, stepsChange: true};
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

// ---- export settings --------------------------------------------------------------

export type ExportFormat = "mp4" | "gif";
export type ExportAspect = "view" | "16:9" | "4:3" | "1:1" | "9:16";

export interface ExportSettings {
    format: ExportFormat;
    /** Short side of the output in pixels (720 = "720p": 1280x720 landscape, 720x1280 portrait). */
    resolution: number;
    aspect: ExportAspect;
    /** Frames per second of a time-history export (a sweep always spans one period). */
    fps: number;
    legend: boolean;
    gizmo: boolean;
}

/** Short-side presets per format. GIF tops out at 1080p: every GIF frame is a
 * full palette image (no inter-frame compression), so larger sizes mostly buy
 * file size and encode time. */
export const RESOLUTION_PRESETS: Record<ExportFormat, number[]> = {
    mp4: [720, 1080, 1440, 2160],
    gif: [360, 480, 720, 1080],
};
export const FPS_PRESETS = [6, 12, 24, 30];
export const ASPECT_PRESETS: ExportAspect[] = ["view", "16:9", "4:3", "1:1", "9:16"];

export const DEFAULT_EXPORT_SETTINGS: ExportSettings = {
    format: "mp4",
    resolution: 1080,
    aspect: "view",
    fps: TIME_HISTORY_EXPORT_FPS,
    legend: true,
    gizmo: true,
};

function aspectRatio(aspect: ExportAspect, viewWidth: number, viewHeight: number): number {
    switch (aspect) {
        case "16:9":
            return 16 / 9;
        case "4:3":
            return 4 / 3;
        case "1:1":
            return 1;
        case "9:16":
            return 9 / 16;
        default:
            return viewWidth > 0 && viewHeight > 0 ? viewWidth / viewHeight : 16 / 9;
    }
}

/**
 * Output size for the settings: ``resolution`` is the short side, the aspect
 * sets the long one, both rounded to even (H.264). If the long side would pass
 * ``maxEdge`` (the GPU's largest drawing buffer), both shrink together so the
 * aspect is kept.
 */
export function resolveExportSize(
    settings: Pick<ExportSettings, "resolution" | "aspect">,
    viewWidth: number,
    viewHeight: number,
    maxEdge = 4096,
): [number, number] {
    const ratio = aspectRatio(settings.aspect, viewWidth, viewHeight);
    const short = settings.resolution;
    let w = ratio >= 1 ? short * ratio : short;
    let h = ratio >= 1 ? short : short / ratio;
    const scale = Math.min(1, maxEdge / Math.max(w, h));
    w *= scale;
    h *= scale;
    return evenSize(Math.round(w), Math.round(h));
}

/** Coerce stored / partial settings onto valid values for their format. */
export function normaliseExportSettings(raw: Partial<ExportSettings> | null | undefined): ExportSettings {
    const s = {...DEFAULT_EXPORT_SETTINGS, ...(raw ?? {})};
    const format: ExportFormat = s.format === "gif" ? "gif" : "mp4";
    const presets = RESOLUTION_PRESETS[format];
    const resolution = presets.includes(s.resolution)
        ? s.resolution
        : presets.reduce((best, r) => (Math.abs(r - s.resolution) < Math.abs(best - s.resolution) ? r : best));
    return {
        format,
        resolution,
        aspect: ASPECT_PRESETS.includes(s.aspect) ? s.aspect : "view",
        fps: FPS_PRESETS.includes(s.fps) ? s.fps : TIME_HISTORY_EXPORT_FPS,
        legend: s.legend !== false,
        gizmo: s.gizmo !== false,
    };
}
