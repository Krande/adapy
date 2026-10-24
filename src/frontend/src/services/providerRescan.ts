// A provider's declared rescan (`asset_collections_refresh`), run through the global job toast.
//
// THREE HOPS, and the browser only sees the last one: the job rescans on the worker, the worker
// re-registers its spec, and the next heartbeat carries the new list to the API. So the job
// finishing is not the list changing. The job is the toast's (`trackJob`: its progress and stage,
// as the provider reports them); the wait for the new list is its FINISHER -- the entry's last
// stage, "waiting for the worker to advertise the new list" -- which reads `GET /plugins` until the
// list changes or two heartbeats have passed (an unchanged list is a legitimate answer).
//
// IN THE BACKGROUND. The entry is persisted (`persist`), so closing the Providers tab, or
// reloading the page, does not stop the toast following it; the finisher is registered here, at
// import, and this module is imported where the app restores its jobs. One rescan per provider:
// the entry's key is the provider's (`rescanStoreKey`), which is also how the tab finds it.

import {assetProviderCollections, type AssetCollectionsRefresh} from "@/services/assetScopeCollections";
import {registerJobFinisher, trackJob} from "@/services/jobTracking";
import {viewerApi} from "@/services/viewerApi";

export const RESCAN_KIND = "provider-rescan";
/** How long to wait for the new list to arrive on a heartbeat after the job. The worker re-reads
 *  its spec every heartbeat (15 s), so two of them and a margin. */
export const RESCAN_ADVERT_TIMEOUT_MS = 45_000;
const ADVERT_POLL_MS = 3000;

/** The toast entry of `providerId`'s rescan. */
export function rescanStoreKey(providerId: string): string {
    return `rescan:${providerId}`;
}

interface RescanData {
    providerId: string;
    /** The advertised collections before the rescan: what "changed" is measured against. */
    before: string[];
}

function isRescanData(v: unknown): v is RescanData {
    const d = v as RescanData | null;
    return !!d && typeof d.providerId === "string" && Array.isArray(d.before);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Enqueue the rescan and hand it to the toast. Returns the toast entry's key. */
export async function startProviderRescan(
    providerId: string,
    refresh: AssetCollectionsRefresh,
    before: readonly string[],
    label: string,
): Promise<string> {
    const {job_id, derived_key} = await viewerApi.pluginJob(refresh.pluginId, {
        options: {...refresh.options, requested_at: new Date().toISOString()},
    });
    return trackJob({
        jobId: job_id,
        scopeUrl: "shared",
        label: `Rescan ${label}`,
        derivedKey: derived_key,
        storeKey: rescanStoreKey(providerId),
        kind: RESCAN_KIND,
        data: {providerId, before: [...before]} satisfies RescanData,
        persist: true,
    });
}

registerJobFinisher(RESCAN_KIND, async ({derivedKey, data, setStage}) => {
    if (!isRescanData(data)) return {message: "Rescanned."};
    let message = "";
    if (derivedKey) {
        try {
            const doc = JSON.parse(new TextDecoder().decode(await viewerApi.getBlob("shared", derivedKey)));
            if (doc && typeof doc.message === "string") message = doc.message;
        } catch {
            // The summary is a courtesy; the list below is the answer.
        }
    }
    const was = JSON.stringify(data.before);
    const started = Date.now();
    for (;;) {
        const waited = Date.now() - started;
        setStage("waiting for the worker to advertise the new list", Math.min(0.99, 0.9 + (0.09 * waited) / RESCAN_ADVERT_TIMEOUT_MS));
        const plugins = await viewerApi.listBackendPlugins().catch(() => ({plugins: []}));
        const now = assetProviderCollections(plugins.plugins ?? []).find((p) => p.providerId === data.providerId)?.collections ?? [];
        const changed = JSON.stringify(now) !== was;
        if (changed || waited > RESCAN_ADVERT_TIMEOUT_MS) {
            return {message: message || (changed ? "Updated." : "No change.")};
        }
        await sleep(ADVERT_POLL_MS);
    }
});
