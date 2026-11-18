// What the progress toast lists, worked out from the conversion store's jobs and the load
// queue: the in-progress rows (an operation's steps stay listed while it runs), the failed
// ones, and the job and name the single scene-load row shows. A plain function, so the rules
// for which job shows where can be tested without rendering the toast.

import type { ConversionJob } from "@/state/conversionStore";

import { isClaimedJob, type IsClaimed } from "../claimedLoads";

/** The conversion store's key for the in-scene GLB download/parse (asyncModelLoader's LOAD_KEY). */
export const MODEL_LOAD_KEY = "model-load";

/** A job as a toast row: the store's entry and the key that addresses it there. */
export type ToastRowJob = ConversionJob & { storeKey: string };

export interface ToastRows {
    inProgress: ToastRowJob[];
    errored: ToastRowJob[];
    /** An operation is running: its steps tell the model download, the load row does not. */
    operationRunning: boolean;
    /** The job whose progress the load row shows, if any. */
    loadJob: ConversionJob | undefined;
    /** The name on the load row; null when there is no load to show. */
    loadName: string | null;
}

export function toastRows(
    jobs: Readonly<Record<string, ConversionJob>>,
    {loadCurrentName, currentClaimed, isClaimed}: {loadCurrentName: string | null; currentClaimed: boolean; isClaimed?: IsClaimed},
): ToastRows {
    // The in-scene GLB download/parse is tracked as a conversionStore job under a
    // sentinel key (asyncModelLoader's LOAD_KEY). It's the SAME activity as the
    // scene load, so it must feed the single load row — not show as its own
    // conversion row (that's the "two toasts for one load" the merge was meant
    // to kill). Direct loadGLTF calls (no load-queue entry) set it without a
    // loadCurrent, so the load row falls back to this job's own name.
    const modelLoadJob = jobs[MODEL_LOAD_KEY];
    const modelLoadActive =
        !!modelLoadJob && (modelLoadJob.status === "queued" || modelLoadJob.status === "running");

    const loadName = currentClaimed
        ? null
        : loadCurrentName ?? (modelLoadActive ? modelLoadJob.derivedKey || "model" : null);
    // In-progress jobs collapse into one toast; errors stay split so each one's
    // traceback + copy button is reachable. Excluded from the conversion rows:
    // (a) the conversion/bake job driving the current scene load, and (b) the
    // model-load GLB-download job — both surface on the single load row.
    // `storeKey` is carried, not dropped. The store is keyed by
    // `${sourceKey}::${target}` (see serverPipeline / useRestoreInflightJobs) and
    // `job.sourceKey` is the bare key — so dismissing by sourceKey deletes
    // nothing and the toast stays on screen forever, which is exactly what it
    // did. The key that addresses the entry has to reach the button.
    const active = (j: {status: string} | undefined) => !!j && (j.status === "queued" || j.status === "running");
    // An operation's steps (`services/operationTracking`) stay listed under it while it runs,
    // finished ones too: a step ending must not take a row out from under the user mid-operation.
    // A COLLAPSED operation's steps are folded into its bar, never listed.
    const inProgress = Object.entries(jobs)
        .filter(([k, j]) =>
            (active(j) || (!!j.parent && active(jobs[j.parent]) && j.status !== "error")) &&
            !(j.parent && jobs[j.parent]?.collapsed && active(jobs[j.parent])) &&
            k !== MODEL_LOAD_KEY &&
            !(loadCurrentName && k.startsWith(loadCurrentName + "::")) &&
            !isClaimedJob(k, isClaimed))
        .map(([k, j]) => ({...j, storeKey: k}));
    // While an operation runs, the model download is one of its steps: its stage line says so
    // (`operationTracking`), and the load row does not come and go beside it per model.
    const operationRunning = Object.entries(jobs).some(([k, j]) => k.startsWith("op:") && !k.includes("#") && active(j));
    // Load-row progress: the conversion/bake job still feeding the load if one is
    // running, else the GLB download job — so the bar runs queue→convert→upload→load.
    const loadJob =
        (loadCurrentName
            ? Object.entries(jobs).find(([k]) => k.startsWith(loadCurrentName + "::"))?.[1]
            : undefined) ?? (modelLoadActive ? modelLoadJob : undefined);
    const errored = Object.entries(jobs)
        .filter(([k, j]) => j.status === "error" && j.sourceKey !== MODEL_LOAD_KEY && !isClaimedJob(k, isClaimed))
        .map(([k, j]) => ({...j, storeKey: k}));
    return {inProgress, errored, operationRunning, loadJob, loadName};
}
