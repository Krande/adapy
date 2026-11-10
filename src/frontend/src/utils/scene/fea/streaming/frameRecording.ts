// FEA streaming: recording played frames and showing them again.
//
// Owns: when a played frame is recorded (an ordinary playback paint of a field the
// recorder can replay), what it is recorded under (source, field and every view
// setting that changes the picture), and showing a recorded frame on the session's
// meshes. The recording itself, and the renderer adapters behind it, live in
// ../playbackFrames.
//
// Inputs: the session handle, the field painted and its warp source.

import type {FeaManifestField} from "@/services/viewerApi";
import type {FeaSessionHandle} from "@/state/modelSession";
import {
    disposeFrameRecorder,
    frameRecorderFor,
    matchingFrameRecorder,
    releaseFrames,
} from "../playbackFrames";
import {translationOffsets, warpValue} from "../warpComponents";
import {recordable} from "./framesKey";
import {syncFeaOverlayVisibility} from "./visibility";

export {framesKey, recordable} from "./framesKey";

/** The warp per source vertex (xyz), as the ordinary paint installed it. Zero when
 *  there is no warp source. */
export function sourceDisplacementOf(
    warp: {field: FeaManifestField; stepValues: Float32Array} | null,
    nPoints: number,
): Float32Array {
    const out = new Float32Array(nPoints * 3);
    if (!warp) return out;
    const nc = warp.field.components.length;
    if (warp.stepValues.length !== nPoints * nc) return out;
    const axes = translationOffsets(warp.field);
    for (let v = 0; v < nPoints; v++) {
        const b = v * nc;
        out[v * 3] = warpValue(warp.stepValues, b, axes[0]);
        out[v * 3 + 1] = warpValue(warp.stepValues, b, axes[1]);
        out[v * 3 + 2] = warpValue(warp.stepValues, b, axes[2]);
    }
    return out;
}

/** An ordinary paint just landed: stop showing recorded frames, and drop a
 *  recording that was made under other settings. */
export function noteOrdinaryPaint(key: string): void {
    releaseFrames();
    if (!matchingFrameRecorder(key)) disposeFrameRecorder();
}

/** Record the frame a playback paint just put on screen. */
export function recordPlayedFrame(args: {
    key: string;
    step: number;
    nFrames: number;
    active: FeaSessionHandle;
    field: FeaManifestField;
    warp: {field: FeaManifestField; stepValues: Float32Array} | null;
}): void {
    const {key, step, nFrames, active, field, warp} = args;
    if (!recordable(field)) return;
    const nPoints = active.basePositions.length / 3;
    const recorder = frameRecorderFor(key, active.mesh, nPoints, active.beamSolidMesh ?? null, nFrames);
    if (!recorder || recorder.has(step)) return;
    recorder.record(step, active.mesh, sourceDisplacementOf(warp, nPoints), active.beamSolidMesh ?? null);
}

/** Show recorded frame `step` if there is one for `key`. True when it is on screen. */
export function showRecordedFrame(key: string, step: number, active: FeaSessionHandle | null, influence: number): boolean {
    if (!active) return false;
    const recorder = matchingFrameRecorder(key);
    if (!recorder?.has(step)) return false;
    if (!recorder.show(step, active.mesh, active.beamSolidMesh ?? null, influence)) return false;
    // The beam lines were re-installed visible; the beams-as-solid and colour
    // toggles decide whether they stay so.
    syncFeaOverlayVisibility();
    return true;
}
