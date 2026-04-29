// Server-side conversion pipeline backed by NATS. Enqueues a job via
// /api/convert and polls /api/convert/{job_id} until the derived blob
// is ready or the job errors out.

import {useConversionStore, ConversionJob} from "@/state/conversionStore";
import {viewerApi, ConvertResponse, TargetFormat, ScopeUrl} from "@/services/viewerApi";

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_ATTEMPTS = 60 * 30; // ~30 min ceiling — generous enough for big IFC

function buildJob(sourceKey: string, payload: ConvertResponse): ConversionJob {
    return {
        sourceKey,
        jobId: payload.job_id,
        derivedKey: payload.derived_key,
        status: payload.status,
        progress: payload.progress,
        stage: payload.stage,
        error: payload.error,
        startedAt: Date.now(),
    };
}

export async function convertViaServer(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat,
    opts?: {step?: number; field?: string},
): Promise<string> {
    // Track jobs per (source, format) so a parallel ifc + xml conversion
    // for the same source doesn't clobber each other in the UI. For FEA
    // picks we further key on (step, field) so two simultaneous picks
    // for the same SIF show distinct progress bars.
    const pickSuffix =
        opts?.step !== undefined && opts?.field !== undefined
            ? `::s${opts.step}.${opts.field}`
            : "";
    const storeKey = `${sourceKey}::${targetFormat}${pickSuffix}`;
    const store = useConversionStore.getState();

    const initial = await viewerApi.convert(scope, sourceKey, targetFormat, opts);
    let job = buildJob(storeKey, initial);
    store.setJob(storeKey, job);

    if (initial.cached || initial.status === "done") {
        store.setJob(storeKey, {...job, status: "done", progress: 1.0, stage: "ready"});
        return initial.derived_key;
    }

    if (!initial.job_id) {
        throw new Error("convert API returned no job_id and no cached result");
    }

    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        let payload: ConvertResponse;
        try {
            payload = await viewerApi.convertStatus(initial.job_id);
        } catch (err) {
            console.warn("convert poll error", err);
            continue;
        }
        job = {
            ...job,
            status: payload.status,
            progress: payload.progress,
            stage: payload.stage,
            error: payload.error,
            derivedKey: payload.derived_key || job.derivedKey,
        };
        store.setJob(storeKey, job);

        if (payload.status === "done") {
            return payload.derived_key || initial.derived_key;
        }
        if (payload.status === "error") {
            throw new Error(payload.error || "conversion failed");
        }
    }
    throw new Error("conversion did not complete within the poll window");
}
