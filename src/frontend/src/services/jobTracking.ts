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
            // it terminal here and cancel the row.
            if (err instanceof ApiError && err.status === 404) {
                const prev = useConversionStore.getState().jobs[storeKey];
                if (prev) {
                    store.setJob(storeKey, {
                        ...prev,
                        status: "cancelled",
                        error: "job is no longer known to the server",
                    });
                }
                void viewerApi
                    .auditLocalUpdate(scopeUrl, jobId, {
                        status: "cancelled",
                        error: "job no longer known to the server",
                    })
                    .catch(() => {});
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
    /** What the toast calls this work. Shown where a conversion shows its source. */
    label: string;
    /** Where the result will land, when the caller knows it up front. */
    derivedKey?: string;
    /** Distinguishes two tracked jobs that share a label. Defaults to the job id,
     * which is always unique — a label alone would let a second run of the same
     * thing overwrite the first one's toast. */
    storeKey?: string;
}

/** Hand a job to the global toast and watch it to completion.
 *
 * Returns the store key, so a caller that wants to dismiss or inspect its own
 * entry can find it. The poll runs detached: the caller may unmount freely, which
 * is the entire point.
 */
export function trackJob(opts: TrackJobOptions): string {
    const storeKey = opts.storeKey || `job:${opts.jobId}`;
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
        startedAt: Date.now(),
    });
    void pollJobUntilTerminal(opts.jobId, storeKey, opts.scopeUrl, {aborted: false});
    return storeKey;
}
