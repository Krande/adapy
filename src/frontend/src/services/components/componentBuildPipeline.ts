// Component-build pipeline backed by the same NATS queue as convert.
// Enqueues a job via /api/components/build and polls /api/convert/
// {job_id} until the derived GLB is ready or the job errors out.
//
// Mirror of services/conversion/serverPipeline.ts. Kept in a separate
// namespace so a parallel CAD conversion + component build don't
// share UI state.

import {
    type ComponentBuildPayload,
    type ConvertResponse,
    type ScopeUrl,
    viewerApi,
} from "@/services/viewerApi";
import {
    type ComponentBuildJob,
    useComponentBuildStore,
} from "@/state/componentBuildStore";

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_ATTEMPTS = 60 * 30; // ~30 min ceiling, matches serverPipeline

function buildJob(
    specName: string,
    jobId: string,
    derivedKey: string,
    payload?: Partial<ConvertResponse>,
): ComponentBuildJob {
    return {
        specName,
        jobId,
        derivedKey,
        status: payload?.status ?? "queued",
        progress: payload?.progress ?? 0,
        stage: payload?.stage ?? "queued",
        error: payload?.error ?? null,
        startedAt: Date.now(),
    };
}

/** Submit a component build and resolve with the derived GLB key when
 *  ready. Throws on backend error or poll-window timeout.
 *
 *  Side effects:
 *  - Sets the job on componentBuildStore for the panel to render.
 *  - Updates the store on each poll tick.
 *  - Does **not** fetch the resulting GLB bytes — caller decides
 *    whether to download via `viewerApi.getBlob(scope, derivedKey)`
 *    (one-shot to load into the scene) or use `viewerApi.blobUrl(...)`
 *    (for an `<img>`-style preview).
 */
export async function buildComponentViaServer(
    payload: ComponentBuildPayload,
    opts?: {scope?: ScopeUrl},
): Promise<string> {
    const store = useComponentBuildStore.getState();
    const specName = payload.spec_name;

    const initial = await viewerApi.componentsBuild(payload, opts);
    let job = buildJob(specName, initial.job_id, initial.derived_key);
    store.setJob(specName, job);

    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        let status: ConvertResponse;
        try {
            status = await viewerApi.convertStatus(initial.job_id);
        } catch (err) {
            console.warn("component build poll error", err);
            continue;
        }
        job = {
            ...job,
            status: status.status,
            progress: status.progress,
            stage: status.stage,
            error: status.error,
            derivedKey: status.derived_key || job.derivedKey,
        };
        store.setJob(specName, job);

        if (status.status === "done") {
            return status.derived_key || initial.derived_key;
        }
        if (status.status === "error") {
            throw new Error(status.error || "component build failed");
        }
    }
    throw new Error("component build did not complete within the poll window");
}
