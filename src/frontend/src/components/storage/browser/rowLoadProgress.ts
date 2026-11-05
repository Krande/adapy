// A storage row's scene load, resolved from the load queue and the conversion-job store
// (the stores the progress toast reads): the real percent and stage while it runs, then
// its error or cancellation. Pure, so the rules can be tested.

import type {ConversionJob} from "@/state/conversionStore";
import {LOAD_JOB_TARGETS} from "@/plugins/loadClaims";
import {stageText} from "@/components/conversion/stageText";

/** The model loader's job for the download and parse; one for every load, so it belongs
 *  to a row only while that row's load is the queue's current one. */
export const MODEL_LOAD_KEY = "model-load";

export function loadJobKeys(name: string): string[] {
    return LOAD_JOB_TARGETS.map((t) => `${name}::${t}`);
}

export type RowLoadPhase = "idle" | "queued" | "loading" | "error" | "cancelled";

export interface RowLoadState {
    phase: RowLoadPhase;
    /** 0..100 while a job reports a number; null while it has none. */
    percent: number | null;
    /** Short stage label, "" when there is none. */
    stage: string;
    /** The error (phase "error") or the cancellation note (phase "cancelled"). */
    message: string | null;
    /** The job a Cancel stops, while the running step can be stopped. */
    cancel: {storeKey: string; jobId: string} | null;
    /** Finished job entries a Dismiss clears. */
    dismissKeys: string[];
}

export const IDLE_ROW_LOAD: RowLoadState = {
    phase: "idle",
    percent: null,
    stage: "",
    message: null,
    cancel: null,
    dismissKeys: [],
};

export interface RowLoadInputs {
    jobs: Readonly<Record<string, ConversionJob>>;
    loadCurrentName: string | null;
    queuedNames: ReadonlySet<string>;
    loadErrors: ReadonlyArray<{name: string; message: string}>;
    /** Loads cancelled from their row, shown as cancelled until dismissed or retried. */
    cancelledNames: ReadonlySet<string>;
}

const isActive = (j: ConversionJob) => j.status === "queued" || j.status === "running";

/** A job's progress as a whole percent. Queued counts as unknown: a bar parked at 0%
 *  reads as stuck, an indeterminate one as waiting. */
export function jobPercent(job: ConversionJob | null | undefined): number | null {
    if (!job || job.status === "queued") return null;
    const p = job.progress;
    if (typeof p !== "number" || !Number.isFinite(p)) return null;
    return Math.max(0, Math.min(100, Math.round(p * 100)));
}

/** The stage label for the row. The model-load job's stages name the model, which the
 *  row already shows, so the name is dropped. */
export function rowStageLabel(storeKey: string, job: ConversionJob): string {
    let s = stageText(job.stage);
    if (storeKey === MODEL_LOAD_KEY && job.derivedKey) {
        s = s
            .split(job.derivedKey)
            .join("")
            .replace(/\s{2,}/g, " ")
            .replace(/\s+([…).,])/g, "$1")
            .trim();
    }
    return s;
}

export function resolveRowLoadState(name: string, inp: RowLoadInputs): RowLoadState {
    const own = loadJobKeys(name)
        .map((k) => [k, inp.jobs[k]] as const)
        .filter((e): e is readonly [string, ConversionJob] => !!e[1]);

    if (inp.loadCurrentName === name) {
        // Cancelled but not yet unwound: a server conversion notices on its next poll,
        // a streaming bake on its next abort check.
        if (inp.cancelledNames.has(name)) return {...IDLE_ROW_LOAD, phase: "loading", stage: "Cancelling…"};
        // The step still moving: the conversion or bake while it runs, then the download.
        const active = own
            .filter(([, j]) => isActive(j))
            .sort((a, b) => b[1].startedAt - a[1].startedAt)[0];
        const modelLoad = inp.jobs[MODEL_LOAD_KEY];
        const picked: readonly [string, ConversionJob] | undefined =
            active ?? (modelLoad && isActive(modelLoad) ? [MODEL_LOAD_KEY, modelLoad] : undefined);
        if (!picked) return {...IDLE_ROW_LOAD, phase: "loading"};
        const [key, job] = picked;
        // A server job stops by id; a streaming bake also stops when its entry is removed,
        // so before it has one. The download has no abort.
        const cancellable = key !== MODEL_LOAD_KEY && (job.jobId !== "" || key.endsWith("::fea"));
        return {
            phase: "loading",
            percent: jobPercent(job),
            stage: rowStageLabel(key, job),
            message: null,
            cancel: cancellable ? {storeKey: key, jobId: job.jobId} : null,
            dismissKeys: [],
        };
    }

    if (inp.queuedNames.has(name)) return {...IDLE_ROW_LOAD, phase: "queued"};

    const dismissKeys = own
        .filter(([, j]) => j.status === "error" || j.status === "cancelled")
        .map(([k]) => k);
    // A cancel outranks the error it causes ("conversion cancelled").
    if (inp.cancelledNames.has(name)) {
        return {...IDLE_ROW_LOAD, phase: "cancelled", message: "Load cancelled", dismissKeys};
    }
    const failedJob = own.find(([, j]) => j.status === "error")?.[1];
    const queueError = inp.loadErrors.find((e) => e.name === name);
    if (failedJob) {
        return {
            ...IDLE_ROW_LOAD,
            phase: "error",
            message: failedJob.error || queueError?.message || "Load failed",
            dismissKeys,
        };
    }
    const cancelledJob = own.find(([, j]) => j.status === "cancelled")?.[1];
    if (cancelledJob) {
        return {
            ...IDLE_ROW_LOAD,
            phase: "cancelled",
            message: cancelledJob.error ? `Cancelled: ${cancelledJob.error}` : "Cancelled",
            dismissKeys,
        };
    }
    if (queueError) {
        return {...IDLE_ROW_LOAD, phase: "error", message: queueError.message || "Load failed", dismissKeys};
    }
    return IDLE_ROW_LOAD;
}
