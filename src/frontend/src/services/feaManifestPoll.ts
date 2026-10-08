// Polling orchestration for the FEA streaming-viewer manifest endpoint.
//
// Pulled out of viewerApi.ts so the contract — 200 = body, 202 = enqueue
// + poll, anything else = throw — can be unit-tested with mock
// fetchers and time control. Originally lived inline in viewerApi but
// silently treated 202 as a cache hit (Response.ok is true for 2xx)
// and tried to parse the queued-job payload as a manifest; this
// module exists in part so that bug class can't recur.

import type {ConvertResponse, FeaManifest, ResultMeta, ScopeUrl} from "./viewerApi";

/** Minimal fetch surface used by the helpers. Tests pass a stub
 * conforming to this shape; production passes a wrapped fetch that
 * attaches the auth bearer. */
export type Fetcher = (
    url: string,
    init?: {signal?: AbortSignal},
) => Promise<Response>;

/** Status-poll surface: tests stub this independently of fetcher so
 * the sequence of (manifest fetch, status polls, manifest re-fetch)
 * is observable. */
export type StatusFn = (jobId: string) => Promise<ConvertResponse>;

class ApiError extends Error {
    constructor(message: string, public status: number, public detail?: string) {
        super(message);
        this.name = "ApiError";
    }
}

interface PollDeps {
    fetcher: Fetcher;
    convertStatus: StatusFn;
    apiBase: string;
    scope: ScopeUrl;
    sourceKey: string;
    signal?: AbortSignal;
    /** Fired on stage / progress changes during the poll loop.
     *
     * Receives the live ``status`` and ``jobId`` alongside the stage
     * label / fractional progress so the call site can synchronise
     * external state (e.g. the global conversion-progress toast)
     * with the bake's queue lifecycle without scraping the URL
     * structure for itself. Status is one of the backend's queue
     * states: 'queued' → 'running' → 'done'. Errors bubble out via
     * the promise rejection, not this callback. */
    onProgress?: (info: {
        jobId: string;
        stage: string;
        progress: number;
        status: "queued" | "running" | "done";
    }) => void;
    /** Polling interval (ms). Tests pass small values; production
     * defaults to 600 ms. */
    pollMs?: number;
    /** Inactivity timeout (ms): give up once the job has shown no change of
     * status, stage or progress for this long. Not a cap on the whole bake --
     * a large deck legitimately bakes for longer than this, and the worker
     * re-emits progress while it does. Tests pass small values; production
     * defaults to 5 min. */
    timeoutMs?: number;
    /** How long (ms) the status poll may keep failing transiently -- the
     * API restarting, a proxy or ingress answering 502/503/504 or a bare
     * 500 while the server is away -- before the loop gives up. The job
     * lives in the queue, not in the API process, so it is still there when
     * the API comes back. Defaults to 2 min. */
    transientGraceMs?: number;
    /** Sleep injection so tests don't have to wait real time. */
    sleep?: (ms: number) => Promise<void>;
    /** Clock injection so tests can advance virtual time. */
    now?: () => number;
}

/** A status-poll failure that says nothing about the job: the request never
 * reached a live API. A network failure (fetch rejects with a TypeError), a
 * gateway's 502/503/504, or a 500 with no body -- what a dev proxy answers
 * when the API behind it is restarting. A 500 the API itself raised carries a
 * body ("Internal Server Error" or a JSON detail), so it is not retried. */
export function isTransientPollError(err: unknown): boolean {
    if (err instanceof TypeError) return true;
    if (typeof err !== "object" || err === null) return false;
    const status = (err as {status?: unknown}).status;
    if (status === 502 || status === 503 || status === 504) return true;
    if (status === 500) {
        const detail = (err as {detail?: unknown}).detail;
        return typeof detail !== "string" || detail.trim() === "";
    }
    return false;
}

async function _readDetail(r: Response): Promise<string> {
    try {
        return await r.text();
    } catch {
        return "";
    }
}

/** Generic 200-or-202+poll orchestrator. Both feaManifest and
 * resultMeta share the same control flow; the only differences are
 * the URL, the cached-body type, and the failure descriptor. */
async function pollEnqueueGet<T>(
    deps: PollDeps,
    label: string,
    buildUrl: () => string,
): Promise<T> {
    const url = buildUrl();
    const r = await deps.fetcher(url, {signal: deps.signal});

    if (r.status === 200) {
        if (!r.ok) {
            // 200 should always be ok; defensive in case fetch shim
            // diverges.
            throw new ApiError(
                `${label} failed: 200 but not ok`,
                r.status,
                await _readDetail(r),
            );
        }
        return (await r.json()) as T;
    }
    if (r.status !== 202) {
        throw new ApiError(
            `${label} failed: ${r.status} ${r.statusText}`,
            r.status,
            await _readDetail(r),
        );
    }

    const queued = (await r.json()) as {job_id: string; stage?: string; progress?: number};
    let stage = queued.stage ?? "queued";
    let progress = queued.progress ?? 0;
    let status: "queued" | "running" | "done" = "queued";
    deps.onProgress?.({jobId: queued.job_id, stage, progress, status});

    const pollMs = deps.pollMs ?? 600;
    const timeoutMs = deps.timeoutMs ?? 5 * 60 * 1000;
    const transientGraceMs = deps.transientGraceMs ?? 2 * 60 * 1000;
    const sleep = deps.sleep ?? ((ms) => new Promise((res) => setTimeout(res, ms)));
    const now = deps.now ?? (() => Date.now());

    // Last time the job visibly moved; the timeout counts from here.
    let lastChangeAt = now();
    // Start of the current run of transient poll failures, if in one.
    let failingSince: number | null = null;
    while (true) {
        if (deps.signal?.aborted) {
            throw new DOMException("aborted", "AbortError");
        }
        await sleep(pollMs);
        const t = now();
        if (t - lastChangeAt > timeoutMs) {
            throw new ApiError(`${label} timed out`, 504);
        }
        let next: ConvertResponse;
        try {
            next = await deps.convertStatus(queued.job_id);
        } catch (err) {
            if (deps.signal?.aborted || !isTransientPollError(err)) throw err;
            if (failingSince === null) failingSince = t;
            if (t - failingSince > transientGraceMs) throw err;
            continue;
        }
        failingSince = null;
        if (next.status === "cancelled") {
            // Server-side cancel (audit row flipped to cancelled by
            // the kill endpoint). Treat as an abort so call sites
            // handle this via the same path as an explicit
            // signal.abort() — silently stop, don't surface an error.
            throw new DOMException(
                `${label} cancelled by server`, "AbortError",
            );
        }
        let nextStatus: "queued" | "running" | "done" = status;
        if (next.status === "queued" || next.status === "running" || next.status === "done") {
            nextStatus = next.status;
        }
        if (next.stage !== stage || next.progress !== progress || nextStatus !== status) {
            stage = next.stage;
            progress = next.progress;
            status = nextStatus;
            lastChangeAt = t;
            deps.onProgress?.({jobId: queued.job_id, stage, progress, status});
        }
        if (next.status === "error") {
            throw new ApiError(
                `${label} failed: ${next.error ?? "unknown"}`,
                500,
                next.error ?? undefined,
            );
        }
        if (next.status === "done") break;
    }

    const r2 = await deps.fetcher(url, {signal: deps.signal});
    if (r2.status !== 200) {
        throw new ApiError(
            `${label} refetch failed: ${r2.status} ${r2.statusText}`,
            r2.status,
            await _readDetail(r2),
        );
    }
    return (await r2.json()) as T;
}

export async function fetchFeaManifest(deps: PollDeps): Promise<FeaManifest> {
    return pollEnqueueGet<FeaManifest>(
        deps,
        `feaManifest(${deps.sourceKey})`,
        () =>
            `${deps.apiBase}/scopes/${encodeURIComponent(deps.scope)}` +
            `/fea/manifest?key=${encodeURIComponent(deps.sourceKey)}`,
    );
}

export async function fetchResultMeta(deps: PollDeps): Promise<ResultMeta> {
    return pollEnqueueGet<ResultMeta>(
        deps,
        `resultMeta(${deps.sourceKey})`,
        () =>
            `${deps.apiBase}/scopes/${encodeURIComponent(deps.scope)}` +
            `/result-meta?key=${encodeURIComponent(deps.sourceKey)}`,
    );
}

export {ApiError};
