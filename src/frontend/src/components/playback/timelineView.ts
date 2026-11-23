// How the transport shows the timeline, beside the clock's own state: the step size of ◀| / |▶ and
// ← / →, and the window of the timeline the scrub bar is zoomed into (null: all of it). Kept out
// of the component so the keyboard, the buttons and the bar read the same, and so it outlives a
// remount. Dependency-free.

import {clampWindow, type PlaybackStepSize, STEP_SIZES, type TimeWindow} from "@/state/playbackTimeline";

export interface TimelineView {
    readonly stepSize: PlaybackStepSize;
    /** The zoomed window, or null for the whole timeline. */
    readonly zoom: TimeWindow | null;
}

let view: TimelineView = {stepSize: "frame", zoom: null};
const listeners = new Set<() => void>();

function set(next: TimelineView): void {
    if (next.stepSize === view.stepSize && next.zoom === view.zoom) return;
    view = next;
    for (const l of listeners) l();
}

export const timelineView = {
    get: (): TimelineView => view,
    subscribe(listener: () => void): () => void {
        listeners.add(listener);
        return () => void listeners.delete(listener);
    },
    setStepSize(size: PlaybackStepSize): void {
        if (size !== "frame" && !(Number.isFinite(size) && size > 0)) return;
        set({...view, stepSize: size});
    },
    /** Zoom into `window` of a timeline `duration` long; null (or the whole of it) zooms out. */
    setZoom(window: TimeWindow | null, duration: number): void {
        if (!window || duration <= 0) return set({...view, zoom: null});
        const zoom = clampWindow(window, duration);
        if (zoom.start <= 1e-9 && zoom.end >= duration - 1e-9) return set({...view, zoom: null});
        if (view.zoom && view.zoom.start === zoom.start && view.zoom.end === zoom.end) return;
        set({...view, zoom});
    },
    resetZoom(): void {
        set({...view, zoom: null});
    },
    /** The window the bar shows: the zoom, kept inside a duration that may have changed, or all of it. */
    window(duration: number): TimeWindow {
        return view.zoom ? clampWindow(view.zoom, duration) : {start: 0, end: Math.max(0, duration)};
    },
    /** Back to the defaults (tests). */
    reset(): void {
        set({stepSize: "frame", zoom: null});
    },
};

export {STEP_SIZES};
