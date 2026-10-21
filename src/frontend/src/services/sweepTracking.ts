// A provider sweep -- or a search export -- in the global toast. Opt-in for a sweep, per sweep
// (Admin -> Providers -> Sweeps); automatic for an export the user just started.
//
// A SWEEP IS NOT A JOB. It is a run the API drives over every collection, one job after another
// (`routes/asset_sweeps.py`), so the toast's job poll (`jobTracking.ts`) has nothing to follow
// across a whole sweep. This polls the sweep instead and folds it into ONE toast entry: overall
// progress across collections, the current collection and its job's live stage.
//
// AN EXPORT IS THE SAME SHAPE. A run over (provider, collection) items, each one job, driven by the
// API (`/assets/search/export`) -- except that up to four items run at once, so there is no single
// "current" item: the stage line names how many are running, and the entry counts what the files
// came to (written, no geometry) because that, not the items, is what the user asked for.
//
// WATCHING, NOT OWNING. The entry carries no job id, so the toast's dismiss button only stops
// watching -- it never cancels the run; that is the runs list's Cancel. What IS offered on the
// entry is "Skip current": give up on the item that is hanging and carry on with the rest
// (`toastSkipAction`). Which runs are watched is remembered in this browser, so the toast comes
// back after a reload.

import {ApiError, viewerApi, type ExportRun, type SweepRun} from "@/services/viewerApi";
import {useConversionStore, type ConvertStatus} from "@/state/conversionStore";
import {toastRunTarget, useRunFocusStore} from "@/state/runFocusStore";
import {useSearchWindowStore} from "@/state/searchWindowStore";
import {useViewerPanelStore} from "@/state/viewerPanelStore";

const POLL_MS = 5000;
const STORAGE_KEY = "ada.sweeps.toast";
const EXPORT_STORAGE_KEY = "ada.exports.toast";
const FINISHED = new Set(["done", "unchanged", "nothing", "error", "skipped"]);
const EXPORT_FINISHED = new Set(["done", "partial", "error", "skipped"]);

const storeKey = (runId: string) => `sweep:${runId}`;
const exportStoreKey = (runId: string) => `export:${runId}`;
const polling = new Set<string>();

/** The latest read of each watched run, by toast key -- what "Skip current" acts on. */
const latest = new Map<string, {kind: "sweep"; run: SweepRun} | {kind: "export"; run: ExportRun}>();

type ToastState = {status: ConvertStatus; progress: number; stage: string; error: string | null};

function readJson(key: string): Record<string, unknown> {
    try {
        const v = JSON.parse(localStorage.getItem(key) || "{}");
        return v && typeof v === "object" ? v : {};
    } catch {
        return {};
    }
}

function writeEntry(key: string, id: string, value: unknown | null): void {
    try {
        const all = readJson(key);
        if (value === null) delete all[id];
        else all[id] = value;
        localStorage.setItem(key, JSON.stringify(all));
    } catch {
        // No storage (private window): the toast still works, it just does not survive a reload.
    }
}

function remembered(): Record<string, string> {
    return readJson(STORAGE_KEY) as Record<string, string>;
}

function remember(runId: string, label: string | null): void {
    writeEntry(STORAGE_KEY, runId, label);
}

/** The terminal mapping both kinds share: done / error / everything else reads as stopped. */
function terminal(status: string, head: string, progress: number, error: string | null, failed: number): ToastState {
    if (status === "done") return {status: "done", progress: 1, stage: head, error: null};
    if (status === "error") return {status: "error", progress, stage: head, error: error ?? `${failed} failed`};
    return {status: "cancelled", progress, stage: `${head} · ${status}`, error: status === "interrupted" ? "the API process driving it stopped" : null};
}

/** One item in flight on a run -- a worker's current job. */
export interface ActiveItem {
    position: number;
    collection: string;
    stage: string;
    /** 0..1, or null while its job reports none. */
    progress: number | null;
}

const clamp01 = (p: number | null | undefined): number | null => (p == null ? null : Math.min(1, Math.max(0, p)));

/** A sweep's items in flight, in run order: one per busy worker when it runs several at once. */
export function sweepActiveItems(run: SweepRun): ActiveItem[] {
    return (run.items ?? [])
        .filter((i) => i.status === "fetching" || i.status === "publishing")
        .map((i) => ({position: i.position, collection: i.collection, stage: i.stage ?? i.status, progress: clamp01(i.progress)}));
}

/** The job ids a run's items are running or have run: a sweep item's fetch and publish jobs, an
 *  export item's job. What tells the toast which of its job entries are that run's workers. */
export function runJobIds(run: SweepRun | ExportRun): Set<string> {
    const ids = new Set<string>();
    for (const i of run.items ?? []) {
        for (const id of "fetch_job_id" in i ? [i.fetch_job_id, i.publish_job_id] : [i.job_id]) {
            if (id) ids.add(id);
        }
    }
    return ids;
}

/** What a run's worker job is called in the toast: its item's collection, and for a sweep item's
 *  publish job, that it is publishing. Null for a job no watched run owns. */
export function workerLabel(jobId: string): string | null {
    if (!jobId) return null;
    for (const seen of latest.values()) {
        for (const i of seen.run.items ?? []) {
            if ("fetch_job_id" in i) {
                if (i.fetch_job_id === jobId) return i.collection;
                if (i.publish_job_id === jobId) return `${i.collection} · publishing`;
            } else if (i.job_id === jobId) {
                return i.collection;
            }
        }
    }
    return null;
}

/** The watched run (its toast key) that `jobId` is a worker of, from the runs' latest reads. */
export function parentRunOf(jobId: string): string | null {
    if (!jobId) return null;
    for (const [key, seen] of latest) if (runJobIds(seen.run).has(jobId)) return key;
    return null;
}

/** The toast's view of a sweep: status, progress over all collections, and a one-line stage. */
export function sweepToastState(run: SweepRun): ToastState {
    const items = run.items ?? [];
    // Out of the collections that will run: ticked-off (`excluded`) ones are not work left to do.
    const excluded = items.filter((i) => i.status === "excluded").length;
    const total = Math.max(1, (run.total || items.length) - excluded);
    const finished = items.filter((i) => FINISHED.has(i.status)).length;
    const active = sweepActiveItems(run);
    const failed = items.filter((i) => i.status === "error").length;
    // Every item in flight counts its share: a sweep may run several at once.
    const within = active.reduce((n, i) => n + (i.progress ?? 0), 0);
    const progress = Math.min(1, (finished + within) / total);
    const head = `${finished}/${total}${failed ? ` · ${failed} failed` : ""}`;
    if (run.status === "running") {
        const where =
            active.length === 1
                ? ` · ${active[0].collection}: ${active[0].stage}`
                : active.length > 1
                  ? ` · ${active.length} running`
                  : "";
        const skipping = run.skip_position != null ? " · skipping…" : "";
        return {status: "running", progress, stage: head + where + skipping, error: null};
    }
    return terminal(run.status, head, progress, run.error, failed);
}

/** The toast's view of an export: items done of total, and what the files came to so far. */
export function exportToastState(run: ExportRun): ToastState {
    const items = run.items ?? [];
    const total = run.total || items.length || 1;
    const finished = items.filter((i) => EXPORT_FINISHED.has(i.status)).length;
    const running = items.filter((i) => i.status === "running");
    // Asking the geometry provider for missing geometry first: shown by its own words (which
    // request job of how many), since its job's stage alone does not say it is a request.
    const requesting = items.filter((i) => i.status === "requesting");
    const failed = items.filter((i) => i.status === "error").length;
    const within = running.reduce((n, i) => n + (i.progress != null ? Math.min(1, Math.max(0, i.progress)) : 0), 0);
    const progress = Math.min(1, (finished + within) / total);
    const files = run.files ?? {written: 0, no_geometry: 0, failed: 0};
    const fileText = [
        `${files.written} written`,
        files.no_geometry ? `${files.no_geometry} no geometry` : "",
        files.failed ? `${files.failed} failed` : "",
    ]
        .filter(Boolean)
        .join(", ");
    const head = `${finished}/${total}${failed ? ` · ${failed} failed` : ""} · ${fileText}`;
    if (run.status === "running") {
        const where =
            running.length === 1
                ? ` · ${running[0].collection}: ${running[0].stage ?? "running"}`
                : running.length > 1
                  ? ` · ${running.length} running`
                  : "";
        const asking = requesting
            .map((i) => ` · ${i.collection}: ${i.message ?? "requesting geometry"}${i.stage ? ` (${i.stage})` : ""}`)
            .join("");
        const skipping = run.skip_position != null ? " · skipping…" : "";
        return {status: "running", progress, stage: head + asking + where + skipping, error: null};
    }
    return terminal(run.status, head, progress, run.error, failed);
}

/** The sweep item "Skip current" means: the one in flight. Null when nothing is, the run is not
 *  running, or a skip is already pending (asking twice would only 409). */
export function sweepSkipTarget(run: SweepRun): number | null {
    if (run.status !== "running" || run.skip_position != null) return null;
    const current = (run.items ?? []).find((i) => i.status === "fetching" || i.status === "publishing");
    return current ? current.position : null;
}

/** The export item "Skip current" means when several run at once: the LOWEST running position --
 *  the one started first, so the likeliest to be the one hanging. The runs list offers a Skip on
 *  every running item for anything finer. */
export function exportSkipTarget(run: ExportRun): number | null {
    if (run.status !== "running" || run.skip_position != null) return null;
    const positions = (run.items ?? [])
        .filter((i) => i.status === "running" || i.status === "requesting")
        .map((i) => i.position);
    return positions.length ? Math.min(...positions) : null;
}

/** A skip offered on a toast entry, or null: what to label it and what it does. `pending` = one
 *  was asked for and the run has not got to it yet. */
export function toastSkipAction(key: string): {label: string; pending: boolean; run: () => Promise<void>} | null {
    const seen = latest.get(key);
    if (!seen || seen.run.status !== "running") return null;
    if (seen.run.skip_position != null) return {label: "skipping…", pending: true, run: async () => {}};
    if (seen.kind === "sweep") {
        const position = sweepSkipTarget(seen.run);
        if (position == null) return null;
        const item = seen.run.items?.find((i) => i.position === position);
        return {
            label: `Skip ${item?.collection ?? "current"}`,
            pending: false,
            run: async () => {
                await viewerApi.adminSkipAssetSweepItem(seen.run.id, position);
                markSkipPending(key, position);
            },
        };
    }
    const position = exportSkipTarget(seen.run);
    if (position == null) return null;
    const item = seen.run.items?.find((i) => i.position === position);
    const run = seen.run;
    return {
        label: `Skip ${item?.collection ?? "current"}`,
        pending: false,
        run: async () => {
            await viewerApi.skipSearchExportItem(run.scope, run.id, position);
            markSkipPending(key, position);
        },
    };
}

/** Show the skip as pending at once rather than at the next poll, and nudge the toast to redraw. */
function markSkipPending(key: string, position: number): void {
    const seen = latest.get(key);
    if (!seen) return;
    if (seen.kind === "sweep") latest.set(key, {kind: "sweep", run: {...seen.run, skip_position: position}});
    else latest.set(key, {kind: "export", run: {...seen.run, skip_position: position}});
    const prev = useConversionStore.getState().jobs[key];
    if (prev) useConversionStore.getState().setJob(key, {...prev, stage: `${prev.stage} · skipping…`});
}

/** One poll loop per watched run, of either kind: read, fold into the entry, stop when it ends or
 *  the entry is dismissed. */
async function pollRun(key: string, read: () => Promise<{state: ToastState}>, forget: () => void): Promise<void> {
    if (polling.has(key)) return;
    polling.add(key);
    try {
        for (;;) {
            if (!useConversionStore.getState().jobs[key]) {
                forget(); // dismissed from the toast: stop watching
                latest.delete(key);
                return;
            }
            try {
                const {state} = await read();
                const prev = useConversionStore.getState().jobs[key];
                if (prev) useConversionStore.getState().setJob(key, {...prev, ...state});
                if (state.status !== "running") {
                    forget();
                    return;
                }
            } catch (err) {
                if (err instanceof ApiError && err.status === 404) {
                    useConversionStore.getState().clearJob(key);
                    latest.delete(key);
                    forget();
                    return;
                }
                // eslint-disable-next-line no-console
                console.warn(`[sweep-tracking] poll ${key} blip`, err);
            }
            await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        }
    } finally {
        polling.delete(key);
    }
}

function seed(key: string, label: string): void {
    if (useConversionStore.getState().jobs[key]) return;
    useConversionStore.getState().setJob(key, {
        sourceKey: label,
        jobId: "", // no job to cancel: dismissing only stops watching
        derivedKey: "",
        status: "running",
        progress: 0,
        stage: "starting",
        error: null,
        startedAt: Date.now(),
    });
}

export function isSweepTracked(runId: string): boolean {
    return !!useConversionStore.getState().jobs[storeKey(runId)];
}

/** Show a sweep in the global toast and keep it current until it finishes or is dismissed. */
export function trackSweep(runId: string, label: string): void {
    const key = storeKey(runId);
    seed(key, label);
    remember(runId, label);
    void pollRun(
        key,
        async () => {
            const run = await viewerApi.adminAssetSweep(runId);
            latest.set(key, {kind: "sweep", run});
            return {state: sweepToastState(run)};
        },
        () => remember(runId, null),
    );
}

export function untrackSweep(runId: string): void {
    useConversionStore.getState().clearJob(storeKey(runId));
    latest.delete(storeKey(runId));
    remember(runId, null);
}

/** Show a tree request -- the mesh export's "request the project trees", a collection request
 *  driven like a sweep -- in the toast. Read through its own scope-checked route, since the sweep
 *  reads behind it are an admin's; no skip is offered for the same reason. Not remembered across a
 *  reload: it is a one-off the user waits on, not a run they come back to. */
export function trackTreeRequest(scope: string, runId: string, label: string): void {
    const key = `trees:${runId}`;
    seed(key, label);
    void pollRun(
        key,
        async () => ({state: sweepToastState(await viewerApi.treeRequestRun(scope, runId))}),
        () => {},
    );
}

export function isExportTracked(runId: string): boolean {
    return !!useConversionStore.getState().jobs[exportStoreKey(runId)];
}

/** Show a search export in the global toast. `scope` is the SEARCHED scope -- where the run lives. */
export function trackExport(scope: string, runId: string, label: string): void {
    const key = exportStoreKey(runId);
    seed(key, label);
    writeEntry(EXPORT_STORAGE_KEY, runId, {scope, label});
    void pollRun(
        key,
        async () => {
            const run = await viewerApi.searchExportRun(scope, runId);
            latest.set(key, {kind: "export", run});
            return {state: exportToastState(run)};
        },
        () => writeEntry(EXPORT_STORAGE_KEY, runId, null),
    );
}

export function untrackExport(runId: string): void {
    useConversionStore.getState().clearJob(exportStoreKey(runId));
    latest.delete(exportStoreKey(runId));
    writeEntry(EXPORT_STORAGE_KEY, runId, null);
}

/** The scope a watched export lives in: from its last read, else from what this browser remembered
 *  (a toast restored after a reload has not been read yet). */
function exportScopeOf(runId: string): string | null {
    const seen = latest.get(exportStoreKey(runId));
    if (seen?.kind === "export") return seen.run.scope;
    const entry = readJson(EXPORT_STORAGE_KEY)[runId] as {scope?: unknown} | undefined;
    return entry && typeof entry.scope === "string" ? entry.scope : null;
}

/** Whether a toast entry has a run table to open ("View run"). */
export function hasRunView(key: string): boolean {
    return toastRunTarget(key, exportScopeOf) !== null;
}

/** Open the table that shows a toast entry's run, expanded on it. */
export function viewToastRun(key: string): void {
    const target = toastRunTarget(key, exportScopeOf);
    if (!target) return;
    useRunFocusStore.getState().setFocus(target.focus);
    if (target.panel === "search") {
        useSearchWindowStore.getState().show();
        return;
    }
    const panel = target.panel;
    const panels = useViewerPanelStore.getState();
    // The admin panel reads its tab once, on mount: already open on another tab, it would stay
    // there. Close it and open it again on the next tick so it mounts on Providers.
    if (panel === "admin" && panels.open === "admin") {
        panels.closePanel();
        setTimeout(() => useViewerPanelStore.getState().openPanel(panel, target.tab ?? undefined), 0);
        return;
    }
    panels.openPanel(panel, target.tab ?? undefined);
}

/** Re-attach the sweeps and exports this browser was watching. Idempotent; called on app load. */
export function resumeTrackedSweeps(): void {
    for (const [runId, label] of Object.entries(remembered())) trackSweep(runId, label);
    for (const [runId, v] of Object.entries(readJson(EXPORT_STORAGE_KEY))) {
        const entry = v as {scope?: unknown; label?: unknown} | null;
        if (entry && typeof entry.scope === "string" && typeof entry.label === "string") trackExport(entry.scope, runId, entry.label);
    }
}
