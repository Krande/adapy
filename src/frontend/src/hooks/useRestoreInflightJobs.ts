// On REST-mode app mount (and on scope change) re-attach to any
// conversions the current user started in the active scope that
// haven't finished yet. Without this hook a long bake the user
// kicked off on Monday and came back to on Tuesday would have its
// progress toast silently missing — the conversion store is purely
// in-memory and page reload wipes it.
//
// Flow:
//   1. Fetch /api/scopes/{scope}/my-jobs which the backend scopes to
//      (current user, current scope, status in [queued, running]).
//   2. Seed useConversionStore with those rows.
//   3. For each entry that still has a job_id, kick off a poll loop
//      that mirrors serverPipeline.ts's pattern — query
//      convertStatus(jobId) on an interval and update the same
//      store entry until status reaches done or error.
//
// Cleanup: scope changes abort outstanding polls and restart with
// the new scope's in-flight set. Polls don't run on initial mount
// before auth is ready — guarded by the scope's truthiness.

import {useEffect} from "react";
import {viewerApi} from "@/services/viewerApi";
import {useConversionStore} from "@/state/conversionStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {runtime} from "@/runtime/config";

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 60 * 30; // ~45 min ceiling, generous for big bakes.

async function pollUntilTerminal(
    jobId: string,
    storeKey: string,
    signal: {aborted: boolean},
): Promise<void> {
    const store = useConversionStore.getState();
    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
        if (signal.aborted) return;
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        if (signal.aborted) return;
        try {
            const status = await viewerApi.convertStatus(jobId);
            const prev = useConversionStore.getState().jobs[storeKey];
            if (!prev) return; // user dismissed it
            store.setJob(storeKey, {
                ...prev,
                status: status.status,
                progress: status.progress,
                stage: status.stage,
                error: status.error,
                derivedKey: status.derived_key || prev.derivedKey,
            });
            if (
                status.status === "done" ||
                status.status === "error" ||
                status.status === "cancelled"
            ) return;
        } catch (err) {
            // Network blip — log and keep polling; the next tick
            // typically succeeds. Don't poison the toast with a
            // false error state.
            // eslint-disable-next-line no-console
            console.warn(`[restore-jobs] poll ${jobId} blip`, err);
        }
    }
}

export function useRestoreInflightJobs(): void {
    const current = useScopeStore((s) => s.current);

    useEffect(() => {
        if (!runtime.isRestMode()) return;
        if (!current) return;
        const scopeUrl = scopeUrlPart(current);
        if (!scopeUrl) return;

        const cancel = {aborted: false};

        (async () => {
            let jobs;
            try {
                jobs = await viewerApi.myJobs(scopeUrl);
            } catch (err) {
                // Endpoint may not exist on older API deployments;
                // fail silently rather than break the rest of the
                // viewer on first paint.
                // eslint-disable-next-line no-console
                console.warn("[restore-jobs] my-jobs fetch failed", err);
                return;
            }
            if (cancel.aborted) return;

            const store = useConversionStore.getState();
            for (const j of jobs) {
                if (!j.job_id) continue;
                // Terminal-state jobs (``error`` / ``cancelled``) are
                // useless to restore: the user already saw the toast
                // in the originating tab, the data is final, and
                // re-registering them undoes a dismissal the user
                // made in another tab. The previous code coerced
                // these to "queued" which briefly flashed a
                // dismissable-as-Cancel toast before the next poll
                // corrected it to "error" — confusing the buttons.
                if (j.status !== "queued" && j.status !== "running") continue;
                // In-browser (WASM) conversions run in a page-bound worker that
                // dies on reload, so a wasm- job can never be reattached or
                // polled (convertStatus only knows worker jobs) — restoring it
                // sits on a "restoring" toast forever and its kill button can't
                // cancel a worker that doesn't exist. Skip it, and best-effort
                // mark the orphaned audit row cancelled so it stops showing as
                // running in the panel.
                if (j.job_id.startsWith("wasm-")) {
                    if (j.status === "running") {
                        void viewerApi
                            .auditLocalUpdate(scopeUrl, j.job_id, {
                                status: "cancelled",
                                error: "interrupted by page reload",
                            })
                            .catch(() => {});
                    }
                    continue;
                }
                // Match serverPipeline.ts's key shape so a fresh
                // conversion of the same source won't clobber the
                // restored entry: ``${sourceKey}::${target_format}``.
                // FEA bakes use the synthetic ``fea`` target.
                const target = j.target_format ?? "fea";
                const storeKey = `${j.key ?? ""}::${target}`;
                if (store.jobs[storeKey]) continue; // already tracked
                store.setJob(storeKey, {
                    sourceKey: j.key ?? "(unknown)",
                    jobId: j.job_id,
                    derivedKey: "",
                    status: j.status,
                    progress: 0,
                    stage: "restoring",
                    error: j.error,
                    startedAt: Date.now(),
                });
                void pollUntilTerminal(j.job_id, storeKey, cancel);
            }
        })();

        return () => {
            cancel.aborted = true;
        };
    }, [current]);
}
