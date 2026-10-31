// Watching a worker job from the app, not from a panel.
//
// WHY THIS EXISTS. A panel that enqueues a job and awaits its own poll loop is
// the only thing watching it, so closing the panel unmounts the watcher and the
// job vanishes from the UI — while carrying on perfectly well server-side. The
// operator is left with no progress, no completion, and no way to tell a job that
// finished from one that died.
//
// The viewer already has the right home for this: `useConversionStore` feeds the
// global toast, which is mounted at app level and outlives every panel, and
// `useRestoreInflightJobs` repopulates it from `my-jobs` on load. A job handed to
// this service therefore survives both closing the panel and a page reload.
//
// ONE POLL LOOP, shared with the restore hook. Two implementations of "poll a job
// to terminal" drift: the 404 handling below was learned the hard way (a job whose
// KV entry aged out polls for the full ceiling and its toast never resolves), and a
// second copy would not have it.

import {ApiError, viewerApi} from "@/services/viewerApi";
import {useConversionStore, type ConvertStatus} from "@/state/conversionStore";

export const POLL_INTERVAL_MS = 1500;
/** ~45 min ceiling, generous for a big bake. */
export const MAX_POLL_ATTEMPTS = 60 * 30;

/** A flag the caller can flip to stop a poll — a scope change, an unmount. */
export interface PollSignal {
    aborted: boolean;
}

/** Poll one job into the conversion store until it reaches a terminal status.
 *
 * Returns when the job is terminal, the entry was dismissed, the signal aborted,
 * or the attempt ceiling is hit. Never throws: a blip must not poison a toast. */
export async function pollJobUntilTerminal(
    jobId: string,
    storeKey: string,
    scopeUrl: string,
    signal: PollSignal,
): Promise<void> {
    const store = useConversionStore.getState();
    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
        if (signal.aborted) return;
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        if (signal.aborted) return;
        // DISMISSAL IS CHECKED BEFORE THE REQUEST, not after it. This check used to
        // live inside the try, after the fetch -- so it was only reached when the
        // fetch SUCCEEDED. With the API unreachable, every attempt threw, took the
        // blip path, and looped: a dismissed toast went on polling for the full
        // ceiling, 45 minutes, against a server it could not reach. Nothing was
        // watching the result, and in a test process the pending timer kept node
        // alive for the same 45 minutes rather than letting the suite exit.
        if (!useConversionStore.getState().jobs[storeKey]) return;
        try {
            const status = await viewerApi.convertStatus(jobId);
            const prev = useConversionStore.getState().jobs[storeKey];
            if (!prev) return; // dismissed while this request was in flight
            store.setJob(storeKey, {
                ...prev,
                status: status.status,
                progress: status.progress,
                stage: status.stage,
                error: status.error,
                derivedKey: status.derived_key || prev.derivedKey,
            });
            if (status.status === "done" || status.status === "error" || status.status === "cancelled") {
                return;
            }
        } catch (err) {
            // A 404 is not a blip: the job is GONE server-side. Its status row lives
            // in the queue's KV, which expires, while the audit row that /my-jobs
            // reads does not — so a job whose KV entry aged out (or was never
            // written) leaves an audit row stuck at `queued` for ever. Treating that
            // as a blip polls it for the full ceiling and the toast never resolves;
            // worse, the row survives reload, so it returns on every refresh. Mark
            // it terminal here and cancel the row -- through the route that owns it: the
            // in-browser audit route only takes `wasm-` ids, so a worker job's row left
            // there was refused and came back, and was polled into a 404 again, on every
            // reload.
            if (err instanceof ApiError && err.status === 404) {
                const prev = useConversionStore.getState().jobs[storeKey];
                if (prev) {
                    store.setJob(storeKey, {
                        ...prev,
                        status: "cancelled",
                        error: "job is no longer known to the server",
                    });
                }
                const closed = jobId.startsWith("wasm-")
                    ? viewerApi.auditLocalUpdate(scopeUrl, jobId, {
                          status: "cancelled",
                          error: "job no longer known to the server",
                      })
                    : viewerApi.cancelMyJob(scopeUrl, jobId);
                void closed.catch(() => {});
                return;
            }
            // Anything else is a genuine blip — log and keep polling; the next tick
            // typically succeeds. Don't poison the toast with a false error state.
            // eslint-disable-next-line no-console
            console.warn(`[job-tracking] poll ${jobId} blip`, err);
        }
    }
}

export interface TrackJobOptions {
    jobId: string;
    /** The scope the job runs in, as a URL part (`shared`, `project:<id>`). */
    scopeUrl: string;
    /** Where the job REALLY ran, when that is not `scopeUrl`: a provider job runs in the provider store
     * (`providers`) whatever scope asked, and the server says so in its answer. Wins over `scopeUrl`. */
    scope?: string;
    /** What the toast calls this work. Shown where a conversion shows its source. */
    label: string;
    /** Where the result will land, when the caller knows it up front. */
    derivedKey?: string;
    /** Distinguishes two tracked jobs that share a label. Defaults to the job id,
     * which is always unique — a label alone would let a second run of the same
     * thing overwrite the first one's toast. */
    storeKey?: string;
    /** What kind of job this is, for a FINISHER registered under it
     * (`registerJobFinisher`): a last phase run once the job is done -- still in the
     * toast, as its final stage. */
    kind?: string;
    /** What the finisher is handed; JSON, since a persisted entry stores it. */
    data?: unknown;
    /** Remember the entry in this browser, so the toast resumes after a reload
     * (`resumeTrackedJobs`). For jobs `my-jobs` cannot restore as themselves: one
     * with a label of its own, or a finisher. */
    persist?: boolean;
    /** The operation this job is a step of (`services/operationTracking`): shown under it. */
    parent?: string;
}

// --- remembered across reloads ----------------------------------------------------------
//
// `my-jobs` restores what the audit log knows -- a job of the CURRENT scope, under its
// source key -- which is right for a conversion and wrong for anything else: a job of
// another scope does not come back at all, and one that does has lost its label and its
// finisher. So a job tracked with `persist` is remembered here, by its store key, and
// `resumeTrackedJobs` re-attaches it on load: the same poll, then its finisher. An entry
// is forgotten once it has finished, was dismissed, or the server no longer knows it.

const STORAGE_KEY = "ada.jobs.toast";

interface Remembered {
    jobId: string;
    scopeUrl: string;
    label: string;
    kind?: string;
    data?: unknown;
    derivedKey?: string;
    startedAt?: number;
}

function readRemembered(): Record<string, Remembered> {
    try {
        const v = JSON.parse(globalThis.localStorage?.getItem(STORAGE_KEY) || "{}");
        return v && typeof v === "object" && !Array.isArray(v) ? v : {};
    } catch {
        return {};
    }
}

function writeRemembered(storeKey: string, entry: Remembered | null): void {
    try {
        const all = readRemembered();
        if (entry === null) delete all[storeKey];
        else all[storeKey] = entry;
        globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(all));
    } catch {
        // No storage (a private window): the toast still works, it just does not survive a reload.
    }
}

/** The entries `resumeTrackedJobs` would re-attach: well-formed ones only. */
export function rememberedJobs(): Record<string, Remembered> {
    const out: Record<string, Remembered> = {};
    for (const [key, v] of Object.entries(readRemembered())) {
        if (v && typeof v.jobId === "string" && v.jobId && typeof v.scopeUrl === "string" && typeof v.label === "string") {
            out[key] = v;
        }
    }
    return out;
}

// --- finishers -----------------------------------------------------------------------------

/** What a finisher is told and may do: its job, and the toast entry's stage. */
export interface JobFinishContext {
    jobId: string;
    storeKey: string;
    scopeUrl: string;
    derivedKey: string;
    data: unknown;
    /** Show what the finishing phase is doing; `progress` 0..1 when it knows. */
    setStage: (stage: string, progress?: number) => void;
}

/** A finisher's answer: the sentence the done entry shows, or why it failed. */
export interface JobFinishResult {
    message?: string;
    error?: string;
}

export type JobFinisher = (ctx: JobFinishContext) => Promise<JobFinishResult>;

const finishers = new Map<string, JobFinisher>();

/** Run `fn` after every tracked job of `kind` is done, before its entry reads as done.
 *  Registered by a module the app loads at start, so a job resumed after a reload --
 *  before the panel that started it is open -- still finishes. */
export function registerJobFinisher(kind: string, fn: JobFinisher): void {
    finishers.set(kind, fn);
}

/** Keys of entries being watched by `watch` right now: a resume does not start a second loop. */
const watching = new Set<string>();

/** Poll to terminal, then (done, not dismissed) the finisher; forget the entry at the end. */
async function watch(storeKey: string, entry: Remembered, persist: boolean): Promise<void> {
    if (watching.has(storeKey)) return;
    watching.add(storeKey);
    try {
        await pollJobUntilTerminal(entry.jobId, storeKey, entry.scopeUrl, {aborted: false});
        const store = useConversionStore.getState();
        const after = store.jobs[storeKey];
        const finisher = entry.kind ? finishers.get(entry.kind) : undefined;
        if (after && after.status === "done" && finisher) {
            // Still running as far as the user is concerned: the job is only part of the work.
            store.setJob(storeKey, {...after, status: "running" as ConvertStatus, stage: "finishing"});
            const setStage = (stage: string, progress?: number) => {
                const prev = useConversionStore.getState().jobs[storeKey];
                if (prev) {
                    useConversionStore.getState().setJob(storeKey, {
                        ...prev,
                        stage,
                        ...(progress !== undefined ? {progress} : {}),
                    });
                }
            };
            let result: JobFinishResult;
            try {
                result = await finisher({
                    jobId: entry.jobId,
                    storeKey,
                    scopeUrl: entry.scopeUrl,
                    derivedKey: entry.derivedKey || after.derivedKey || "",
                    data: entry.data,
                    setStage,
                });
            } catch (e) {
                result = {error: e instanceof Error ? e.message : String(e)};
            }
            const prev = useConversionStore.getState().jobs[storeKey];
            if (prev) {
                useConversionStore.getState().setJob(storeKey, {
                    ...prev,
                    status: (result.error ? "error" : "done") as ConvertStatus,
                    progress: result.error ? prev.progress : 1,
                    stage: result.message ?? (result.error ? prev.stage : "done"),
                    error: result.error ?? null,
                });
            }
        }
    } finally {
        watching.delete(storeKey);
        if (persist) writeRemembered(storeKey, null);
    }
}

/** Hand a job to the global toast and watch it to completion.
 *
 * Returns the store key, so a caller that wants to dismiss or inspect its own
 * entry can find it. The poll runs detached: the caller may unmount freely, which
 * is the entire point. With `persist` it also outlives a reload.
 */
export function trackJob(opts: TrackJobOptions): string {
    const storeKey = opts.storeKey || `job:${opts.jobId}`;
    const startedAt = Date.now();
    useConversionStore.getState().setJob(storeKey, {
        sourceKey: opts.label,
        jobId: opts.jobId,
        derivedKey: opts.derivedKey || "",
        // `queued` and not `running`: the worker may not have picked it up yet, and
        // claiming otherwise makes a job waiting for a busy pool look stuck.
        status: "queued" as ConvertStatus,
        progress: 0,
        stage: "queued",
        error: null,
        startedAt,
        ...(opts.parent ? {parent: opts.parent} : {}),
    });
    const entry: Remembered = {
        jobId: opts.jobId,
        scopeUrl: opts.scope ?? opts.scopeUrl,
        label: opts.label,
        ...(opts.kind ? {kind: opts.kind} : {}),
        ...(opts.data !== undefined ? {data: opts.data} : {}),
        ...(opts.derivedKey ? {derivedKey: opts.derivedKey} : {}),
        startedAt,
    };
    if (opts.persist) writeRemembered(storeKey, entry);
    void watch(storeKey, entry, !!opts.persist);
    return storeKey;
}

/** Re-attach the jobs this browser was watching with `persist` (see above). Idempotent:
 *  an entry already in the toast, or already being watched, is left alone. Called on app load. */
export function resumeTrackedJobs(): void {
    const store = useConversionStore.getState();
    for (const [storeKey, entry] of Object.entries(rememberedJobs())) {
        if (watching.has(storeKey)) continue;
        if (!store.jobs[storeKey]) {
            store.setJob(storeKey, {
                sourceKey: entry.label,
                jobId: entry.jobId,
                derivedKey: entry.derivedKey || "",
                status: "running" as ConvertStatus,
                progress: 0,
                stage: "restoring",
                error: null,
                startedAt: entry.startedAt || Date.now(),
            });
        }
        void watch(storeKey, entry, true);
    }
}

/** Whether any toast entry already follows `jobId` -- so a second restore path (`my-jobs`)
 *  does not add a duplicate under another key. */
export function isJobTracked(jobId: string): boolean {
    return Object.values(useConversionStore.getState().jobs).some((j) => j.jobId === jobId);
}
