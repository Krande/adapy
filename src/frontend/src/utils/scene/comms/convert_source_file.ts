import {useConversionStore, ConversionJob, ConvertStatus} from "../../../state/conversionStore";

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_ATTEMPTS = 60 * 30; // ~30 min ceiling — generous enough for big IFC

export type TargetFormat = "glb" | "ifc" | "xml";

interface ConvertResponse {
    job_id: string;
    source_key: string;
    derived_key: string;
    target_format?: TargetFormat;
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
 * the derived blob is ready. Updates the conversion store as the job
 * progresses so the UI can render a progress hint. Returns the
 * derived storage key so callers can download or re-fetch it.
 *
 * Throws if the API rejects the source, the job errors out, or the
 * poll loop exceeds MAX_POLL_ATTEMPTS.
 */
export async function ensureConverted(
    sourceKey: string,
    targetFormat: TargetFormat = "glb",
): Promise<string> {
    if (!convertEnabled()) {
        throw new Error("conversion not enabled on this deployment");
    }

    // Track jobs per (source, format) so a parallel ifc + xml conversion
    // for the same source doesn't clobber each other in the UI.
    const storeKey = `${sourceKey}::${targetFormat}`;
    const store = useConversionStore.getState();

    const r = await fetch(`${apiBase()}/convert`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({source_key: sourceKey, target_format: targetFormat}),
    });
    if (!r.ok) {
        const detail = await r.text().catch(() => "");
        throw new Error(`convert enqueue failed: ${r.status} ${detail}`);
    }
    const initial = await r.json() as ConvertResponse;
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
            payload = await pollOnce(initial.job_id);
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

// Backwards-compatible wrapper for the GLB-for-viewing flow.
export async function ensureConvertedGlb(sourceKey: string): Promise<void> {
    await ensureConverted(sourceKey, "glb");
}

export async function fetchSupportedTargets(sourceKey: string): Promise<TargetFormat[]> {
    const url = `${apiBase()}/convert/targets?source_key=${encodeURIComponent(sourceKey)}`;
    const r = await fetch(url);
    if (!r.ok) {
        return [];
    }
    const body = await r.json() as {targets: TargetFormat[]};
    return body.targets || [];
}
