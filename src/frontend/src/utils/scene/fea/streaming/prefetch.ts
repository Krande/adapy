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

/** What the frame fetchers need: the manifest, the field played, the warp
 *  preference and the transport's fetchers. */
export interface FrameSource {
    manifest: FeaManifest;
    field: FeaManifestField;
    warpEnabled: boolean;
    fetcher: FeaFetcher;
    rangeFetcher: FeaRangeFetcher;
    cacheKey: string;
}

/** The fetches that put slot `step` of the played field -- its colour values and
 *  the displacement that warps it -- into the step caches. Empty for a combination
 *  slot (the loader prefetches the next case; materialising one is not a read). */
function frameFetches(src: FrameSource, step: number): Promise<unknown>[] {
    const {manifest, field, warpEnabled, fetcher, rangeFetcher, cacheKey} = src;
    const ref = slotRef(manifest, field, step);
    if (isCaseRef(ref)) return [];
    const stored = ref.stored;
    const out: Promise<unknown>[] = [];
    if (field.per_type && field.per_type.length > 0) {
        for (const bucket of field.per_type) {
            out.push(fetchElemFieldStep(rangeFetcher, fetcher, bucket, stored, cacheKey));
        }
    } else if (field.blob) {
        out.push(fetchFieldStep(rangeFetcher, fetcher, field, stored, cacheKey));
    }
    // The same blob and step as the colour field when it warps itself: the cache
    // answers the second request with the first one's promise.
    const warpField = warpFieldFor(manifest, field, warpEnabled);
    if (warpField?.blob) {
        out.push(fetchFieldStep(rangeFetcher, fetcher, warpField, Math.min(stored, warpField.n_steps - 1), cacheKey));
    }
    return out;
}

/**
 * Start fetching `steps` of the played field into the step caches. Fire-and-forget:
 * a failure here is not an error -- the frame's own load fetches again and reports
 * it.
 */
export function prefetchFieldSteps(args: FrameSource & {steps: number[]}): void {
    const ignore = () => undefined;
    for (const step of args.steps) for (const p of frameFetches(args, step)) p.catch(ignore);
}

/** Bytes one frame of `field` takes in the step caches: its colour values plus the
 *  displacement that warps it, when that is another field. */
export function frameBytes(manifest: FeaManifest, field: FeaManifestField, warpEnabled: boolean): number {
    let bytes = field.per_type?.length
        ? field.per_type.reduce((sum, bucket) => sum + bucket.blob.stride_bytes, 0)
        : field.blob?.stride_bytes ?? 0;
    const warpField = warpFieldFor(manifest, field, warpEnabled);
    if (warpField && warpField !== field) bytes += warpField.blob?.stride_bytes ?? 0;
    return bytes;
}

export interface PreloadProgress {
    /** Frames in the caches so far. */
    loaded: number;
    /** Frames this preload is fetching: every frame, or as many as the budget holds. */
    total: number;
    /** Frames in the history. Larger than ``total`` when the budget is the limit. */
    nFrames: number;
    done: boolean;
}

/**
 * Fetch every frame of a time history into the step caches -- or as many as the
 * byte budget holds, starting at `startStep` and going forward, which is the order
 * play will want them in. A few requests at a time; `signal` stops it (a new field,
 * a new model). Resolves with the final progress.
 *
 * After it, play paints from memory from the first frame on instead of from the
 * second loop on.
 */
export async function preloadFrames(
    src: FrameSource & {
        nFrames: number;
        startStep: number;
        budgetBytes: number;
        onProgress?: (p: PreloadProgress) => void;
        signal?: AbortSignal;
        concurrency?: number;
    },
): Promise<PreloadProgress> {
    const {nFrames, startStep, budgetBytes, onProgress, signal, concurrency = 3} = src;
    const perFrame = Math.max(1, frameBytes(src.manifest, src.field, src.warpEnabled));
    // Some headroom: the caches hold more than this one field (the other fields'
    // shown steps, a case or two).
    const fits = Math.max(1, Math.floor((budgetBytes * 0.85) / perFrame));
    const total = Math.min(nFrames, fits);
    const order: number[] = [];
    for (let i = 0; i < total; i++) order.push((startStep + i) % nFrames);

    const progress: PreloadProgress = {loaded: 0, total, nFrames, done: false};
    onProgress?.({...progress});
    let next = 0;
    const worker = async () => {
        while (next < order.length && !signal?.aborted) {
            const step = order[next++];
            await Promise.all(frameFetches(src, step)).catch(() => undefined);
            if (signal?.aborted) return;
            progress.loaded++;
            onProgress?.({...progress});
        }
    };
    await Promise.all(Array.from({length: Math.min(concurrency, order.length)}, worker));
    progress.done = !signal?.aborted;
    onProgress?.({...progress});
    return progress;
}
