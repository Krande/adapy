// A loaded model's node animation as a track of the playback clock: registered when a model with
// an animation is on screen, removed when it is not, whichever viewport draws it. The viewport says
// what it can play (`ViewportApi.modelAnimation`); this module only keeps the clock's track in step.
// No three.js in this module.
import {playbackClock, type PlaybackTrackHandle} from "@/state/playbackClock";
import {activeViewport, onActiveViewportChange, onModelAnimationChange, type ModelAnimation} from "@/viewport/api/viewportApi";

export const MODEL_ANIMATION_TRACK = "core:model-animation";

let bound = false;
let shown: ModelAnimation | null = null;
let track: PlaybackTrackHandle | null = null;

function sync(): void {
    const animation = activeViewport()?.modelAnimation() ?? null;
    if (animation === shown) return;
    shown = animation;
    track?.remove();
    track = animation
        ? playbackClock.addTrack({id: MODEL_ANIMATION_TRACK, label: "Model animation", duration: animation.duration, show: animation.showTime})
        : null;
}

/** Start keeping the track in step. Each viewport calls it when it mounts; once is enough. */
export function bindModelAnimationTrack(): void {
    if (bound) return;
    bound = true;
    onActiveViewportChange(sync);
    onModelAnimationChange(sync);
    sync();
}
