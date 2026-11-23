// "Capture frame": a screenshot of the view at an exact moment of the playback clock, saved with
// that moment in its name. The clock is paused and set to the moment first, then one frame is let
// through before the picture is taken: every track has shown the moment by then, and so has
// whatever a viewport draws from the time in its own frame loop (the native renderer's effects are
// pure functions of the time it is given in a frame), so the picture is the same however the
// moment was reached. The picture itself is the viewport's own screenshot (`saveScreenshot`), on
// either viewport. No three.js in this module.

import {playbackClock} from "@/state/playbackClock";
import {captureFilename} from "@/state/playbackTimeline";
import {activeViewport} from "@/viewport/api/viewportApi";

/** Resolves after `count` animation frames (timers where there are none, as in tests). */
export function afterFrames(count = 2): Promise<void> {
    return new Promise((resolve) => {
        const raf: ((cb: () => void) => unknown) | undefined =
            typeof requestAnimationFrame === "function" ? requestAnimationFrame : undefined;
        let left = count;
        const next = () => {
            if (left-- <= 0) return resolve();
            if (raf) raf(next);
            else setTimeout(next, 16);
        };
        next();
    });
}

export interface CaptureFrameOptions {
    /** The moment, in seconds; default: the clock's time now. */
    time?: number;
    /** The start of the file's name ("frame"). */
    prefix?: string;
}

/** Pause, go to the moment, let a frame be drawn and save the view as "<prefix>_t<mm>m<ss.mmm>s.png".
 *  Resolves the file's name, or null with no viewport on screen. */
export async function captureFrame(options: CaptureFrameOptions = {}): Promise<string | null> {
    const viewport = activeViewport();
    if (!viewport) return null;
    playbackClock.pause();
    if (options.time !== undefined) playbackClock.seek(options.time);
    await afterFrames(2);
    const name = captureFilename(playbackClock.time(), options.prefix);
    (activeViewport() ?? viewport).saveScreenshot(name);
    return name;
}
