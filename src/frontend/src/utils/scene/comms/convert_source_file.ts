import {useConversionStore, ConversionJob, ConvertStatus} from "@/state/conversionStore";
import {useExperimentalStore} from "@/state/experimentalStore";
import {convertIfcViaPyodide} from "@/utils/pyodide/pyodide_converter";
import {runtime} from "@/runtime/config";

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
    return runtime.apiBase();
}

function convertEnabled(): boolean {
    return runtime.convertEnabled();
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

function shouldUsePyodide(sourceKey: string, targetFormat: TargetFormat): boolean {
    if (targetFormat !== "glb") return false;
    if (!sourceKey.toLowerCase().endsWith(".ifc")) return false;
    return useExperimentalStore.getState().pyodideConverter;
}

async function convertViaPyodideAndUpload(sourceKey: string): Promise<string> {
    const storeKey = `${sourceKey}::glb`;
    const store = useConversionStore.getState();
    const job: ConversionJob = {
        sourceKey: storeKey,
        jobId: "pyodide",
        derivedKey: "",
        status: "running",
        progress: 0.05,
        stage: "fetching source",
        error: null,
        startedAt: Date.now(),
    };
    store.setJob(storeKey, job);

    const sourceUrl = `${apiBase()}/blobs/${encodeURIComponent(sourceKey)}`;
    const r = await fetch(sourceUrl);
    if (!r.ok) throw new Error(`fetch source failed: ${r.status}`);
    const sourceBuf = await r.arrayBuffer();

    store.setJob(storeKey, {...job, progress: 0.15, stage: "tessellating in browser"});

    const glb = await convertIfcViaPyodide(sourceBuf, {
        onLog: (msg) => store.setJob(storeKey, {
            ...store.jobs[storeKey] || job,
            stage: msg,
        }),
    });

    store.setJob(storeKey, {
        ...store.jobs[storeKey] || job,
        progress: 0.9,
        stage: "uploading derived",
    });

    const derivedKey = `_derived/${sourceKey}.glb`;
    const put = await fetch(`${apiBase()}/blobs/${encodeURIComponent(derivedKey)}`, {
        method: "PUT",
        body: glb,
        headers: {"Content-Type": "application/octet-stream"},
    });
    if (!put.ok) {
        const detail = await put.text().catch(() => "");
        throw new Error(`upload derived failed: ${put.status} ${detail}`);
    }

    store.setJob(storeKey, {
        ...store.jobs[storeKey] || job,
        progress: 1.0,
        stage: "ready",
        status: "done",
        derivedKey,
    });
    return derivedKey;
}

/**
 * Enqueue a conversion for a source key and resolve when the derived
 * blob is ready. Routes through the Pyodide in-browser path when the
 * experimental toggle is on AND the source/target combination is
 * supported there; otherwise hits the server-side NATS pipeline.
 *
 * Throws if the API rejects the source, the job errors out, or the
 * poll loop exceeds MAX_POLL_ATTEMPTS.
 */
export async function ensureConverted(
    sourceKey: string,
    targetFormat: TargetFormat = "glb",
): Promise<string> {
    if (shouldUsePyodide(sourceKey, targetFormat)) {
        return await convertViaPyodideAndUpload(sourceKey);
    }

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
