import {useConversionStore, ConversionJob, ConvertStatus} from "../../../state/conversionStore";

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_ATTEMPTS = 60 * 30; // ~30 min ceiling — generous enough for big IFC

interface ConvertResponse {
    job_id: string;
    source_key: string;
    derived_key: string;
    status: ConvertStatus;
    progress: number;
    stage: string;
    error: string | null;
    cached: boolean;
}

function apiBase(): string {
    return ((window as any).API_BASE || "/api").replace(/\/+$/, "");
}

function convertEnabled(): boolean {
    return Boolean((window as any).CONVERT_ENABLED);
}

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

async function pollOnce(jobId: string): Promise<ConvertResponse> {
    const r = await fetch(`${apiBase()}/convert/${encodeURIComponent(jobId)}`);
    if (!r.ok) {
        throw new Error(`convert status fetch failed: ${r.status} ${r.statusText}`);
    }
    return await r.json() as ConvertResponse;
}

/**
 * Enqueue a server-side conversion for a source key and resolve when
 * the derived GLB is ready. Updates the conversion store as the job
 * progresses so the UI can render a progress hint.
 *
 * Throws if the API rejects the source, the job errors out, or the
 * poll loop exceeds MAX_POLL_ATTEMPTS.
 */
export async function ensureConvertedGlb(sourceKey: string): Promise<void> {
    if (!convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }

    const store = useConversionStore.getState();

    // Kick off (or reuse) the job.
    const r = await fetch(`${apiBase()}/convert`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({source_key: sourceKey}),
    });
    if (!r.ok) {
        const detail = await r.text().catch(() => "");
        throw new Error(`convert enqueue failed: ${r.status} ${detail}`);
    }
    const initial = await r.json() as ConvertResponse;
    let job = buildJob(sourceKey, initial);
    store.setJob(sourceKey, job);

    if (initial.cached || initial.status === "done") {
        store.setJob(sourceKey, {...job, status: "done", progress: 1.0, stage: "ready"});
        return;
    }

    if (!initial.job_id) {
        throw new Error("convert API returned no job_id and no cached result");
    }

    // Poll until done or error.
    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        let payload: ConvertResponse;
        try {
            payload = await pollOnce(initial.job_id);
        } catch (err) {
            // transient errors shouldn't kill the whole flow; keep polling.
            console.warn("convert poll error", err);
            continue;
        }
        job = {
            ...job,
            status: payload.status,
            progress: payload.progress,
            stage: payload.stage,
            error: payload.error,
        };
        store.setJob(sourceKey, job);

        if (payload.status === "done") {
            return;
        }
        if (payload.status === "error") {
            throw new Error(payload.error || "conversion failed");
        }
    }
    throw new Error("conversion did not complete within the poll window");
}
