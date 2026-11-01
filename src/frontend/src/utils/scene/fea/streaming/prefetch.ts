// FEA streaming: read-ahead for time-history playback.
//
// Owns: warming the per-step blob caches (feaFieldBlob / feaElemFieldBlob) for the
// frames play is about to show, so fetching frame N+1 overlaps painting frame N
// instead of following it. Without this every frame paid a full network round trip
// before it could paint -- and its latency, which varies frame to frame, was the
// rhythm of the animation.
//
// Inputs: the manifest, the field being played, the warp preference, the fetchers.
// Holds no state of its own: the caches it fills dedupe in-flight requests, so
// asking for a step that is already on its way costs nothing.

import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {fetchFieldStep} from "@/services/feaFieldBlob";
import type {FeaFetcher, FeaRangeFetcher} from "@/services/fea/feaFetcher";
import {isCaseRef} from "@/services/fea/feaStepRef";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {slotRef} from "../caseSteps";
import {findDisplacementField} from "./warp";

/** How many frames play reads ahead of the one it is showing. Enough to ride out a
 *  slow request at 12 fps; few enough that a pause does not leave many requests
 *  running for frames nobody will see soon. */
export const PLAYBACK_READ_AHEAD = 4;

/** The steps after `step` play will show next, wrapping at `nSteps`. */
export function upcomingSteps(step: number, nSteps: number, count: number = PLAYBACK_READ_AHEAD): number[] {
    const out: number[] = [];
    for (let i = 1; i <= Math.min(count, nSteps - 1); i++) out.push((step + i) % nSteps);
    return out;
}

/** The field whose values deform the mesh while `field` is shown, or null. Mirrors
 *  resolveWarpSource's choice without fetching anything. */
function warpFieldFor(manifest: FeaManifest, field: FeaManifestField, warpEnabled: boolean): FeaManifestField | null {
    if (!warpEnabled || field.category === "reaction") return null;
    if (field.category === "displacement") return field;
    return findDisplacementField(manifest);
}

/**
 * Start fetching the colour and warp values of `steps` of `field` into the step
 * caches. Fire-and-forget: a failure here is not an error -- the frame's own load
 * fetches again and reports it. Combination slots are skipped (the loader already
 * prefetches the next case, and materialising them is not a read).
 */
export function prefetchFieldSteps(args: {
    manifest: FeaManifest;
    field: FeaManifestField;
    steps: number[];
    warpEnabled: boolean;
    fetcher: FeaFetcher;
    rangeFetcher: FeaRangeFetcher;
    cacheKey: string;
}): void {
    const {manifest, field, steps, warpEnabled, fetcher, rangeFetcher, cacheKey} = args;
    const warpField = warpFieldFor(manifest, field, warpEnabled);
    const ignore = () => undefined;
    for (const step of steps) {
        const ref = slotRef(manifest, field, step);
        if (isCaseRef(ref)) continue;
        const stored = ref.stored;
        if (field.per_type && field.per_type.length > 0) {
            for (const bucket of field.per_type) {
                fetchElemFieldStep(rangeFetcher, fetcher, bucket, stored, cacheKey).catch(ignore);
            }
        } else if (field.blob) {
            fetchFieldStep(rangeFetcher, fetcher, field, stored, cacheKey).catch(ignore);
        }
        // The same blob and step as the colour field when it warps itself: the
        // cache answers the second request with the first one's promise.
        if (warpField?.blob) {
            const warpStep = Math.min(stored, warpField.n_steps - 1);
            fetchFieldStep(rangeFetcher, fetcher, warpField, warpStep, cacheKey).catch(ignore);
        }
    }
}
