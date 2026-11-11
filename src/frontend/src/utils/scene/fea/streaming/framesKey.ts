// FEA streaming: what a recording of played frames is valid for.
//
// Owns: the key a recording is kept under (the source, the field and every view setting that
// changes the picture) and which fields can be recorded at all. Shared by the two recorders: the
// three.js loader's (./frameRecording.ts) and the layer session's (./layerFrames.ts).
//
// No three.js in this module or in anything it imports.

import type {FeaManifestField} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";

/** What a recording is valid for: the field painted and every setting that changes
 *  its colours or its warp. Not the warp SCALE -- that is the morph influence,
 *  applied when a frame is shown. */
export function framesKey(sourceName: string, fieldName: string | null, reduction: string | null): string {
    const s = useFeaAnimationStore.getState();
    return JSON.stringify([
        sourceName,
        fieldName,
        reduction,
        s.colormap,
        s.contour,
        s.layer,
        s.ipReduction,
        s.nodalAverage,
        s.warpEnabled,
        s.envelopeMode,
    ]);
}

/** Fields whose paint the recorder can replay. Result-point fields also draw point
 *  markers, which it does not keep. */
export function recordable(field: FeaManifestField): boolean {
    return field.support !== "result_point" && field.support !== "line_result_point";
}
