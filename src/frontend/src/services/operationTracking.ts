// One toast entry for work that runs as several jobs one after another.
//
// WHY. "Request into the scene" is a provider job, then core's publish, then a build per node, then
// the model's download -- each its own job, each its own toast row while it runs. The toast shows
// only what is queued or running, so between two jobs it had nothing to show: it vanished and came
// back with the next row, over and over, for one thing the user asked for.
//
// AN OPERATION is a parent entry that stays up from the first step to the last. Its jobs are tracked
// with it as their `parent`, so the toast draws them as rows under it (finished ones too, while it
// runs) and never as toasts of their own. What it waits for is HELD: a request holds it until its
// publish lands, a pending load until the model is in the scene. When the last hold is released
// the operation ends -- done, or failed when a step failed -- and its rows go with it.
//
// Its progress is the mean of its steps' (a finished step counts whole), so it can step back when
// a later step is added -- unless the operation says its own (`progress`, e.g. models done of a
// restore's total). Its stage is what the operation says it is doing, else the running step's; a
// model download in flight (the scene's one load row, which the toast folds into a running
// operation) is added to it.
//
// COLLAPSED, its steps are not listed at all: one row, one bar (a scene restore of many models).

import {trackJob} from "@/services/jobTracking";
import {useConversionStore, type ConversionJob} from "@/state/conversionStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

export interface Operation {
    /** The parent entry's store key (`op:<id>`). */
    readonly key: string;
    /** Track a job as one step of this operation: a row under it. */
    trackJob(opts: {jobId: string; label: string; derivedKey?: string; scope?: string}): void;
    /** Keep the operation up until the returned release is called (once; later calls do nothing). */
    hold(): () => void;
    /** What the operation is doing, shown in place of the running step's stage; null to clear. */
    stage(text: string | null): void;
    /** Record a failure: the operation ends as an error, with this as the reason. */
    fail(error: string): void;
    /** The operation's own progress 0..1, in place of its steps' mean; null to go back to that. */
    progress(fraction: number | null): void;
}

export interface OperationOptions {
    /** Steps folded into the one bar, never listed (`ConversionJob.collapsed`). */
    collapsed?: boolean;
}

const TERMINAL = new Set(["done", "error", "cancelled"]);

/** The scene's model download row (`asyncModelLoader`'s key): folded into a running operation. */
export const MODEL_LOAD_KEY = "model-load";

let seq = 0;

/** The parent entry's view of its steps: mean progress, and the running step's stage. */
export function operationProgress(steps: readonly ConversionJob[]): {progress: number; stage: string | null} {
    if (!steps.length) return {progress: 0, stage: null};
    const progress = steps.reduce((n, s) => n + (TERMINAL.has(s.status) ? 1 : Math.min(1, Math.max(0, s.progress || 0))), 0);
    const running = [...steps].reverse().find((s) => s.status === "running" || s.status === "queued");
    return {
        progress: progress / steps.length,
        stage: running ? `${running.sourceKey}${running.stage ? `: ${running.stage}` : ""}` : null,
    };
}

export function beginOperation(label: string, options: OperationOptions = {}): Operation {
    const key = `op:${Date.now().toString(36)}-${(seq++).toString(36)}`;
    const scopeUrl = scopeUrlPart(useScopeStore.getState().current);
    const store = () => useConversionStore.getState();
    let holds = 0;
    let ended = false;
    let ownStage: string | null = null;
    let ownProgress: number | null = null;
    let failure: string | null = null;

    store().setJob(key, {
        sourceKey: label,
        jobId: "", // nothing to cancel as a whole: dismissing only stops showing it
        derivedKey: "",
        status: "running",
        progress: 0,
        stage: "starting",
        error: null,
        startedAt: Date.now(),
        ...(options.collapsed ? {collapsed: true} : {}),
    });

    const steps = () => Object.entries(store().jobs).filter(([, j]) => j.parent === key);

    const refresh = () => {
        const self = store().jobs[key];
        if (!self || ended) return;
        const fromSteps = operationProgress(steps().map(([, j]) => j));
        const download = store().jobs[MODEL_LOAD_KEY];
        const downloading = download && (download.status === "running" || download.status === "queued") ? download.stage : null;
        const base = ownStage ?? fromSteps.stage ?? self.stage;
        const next = {
            ...self,
            progress: ownProgress ?? fromSteps.progress,
            stage: downloading && !base.includes(downloading) ? `${base} · ${downloading}` : base,
        };
        if (next.progress !== self.progress || next.stage !== self.stage) store().setJob(key, next);
    };
    // Follows its steps for as long as it is up. Dismissed, it stops, and its rows go with it -- the
    // jobs themselves carry on server-side; only the watching ends.
    const unsubscribe = useConversionStore.subscribe((s, prev) => {
        if (!s.jobs[key]) {
            unsubscribe();
            if (!ended) for (const [k] of steps()) store().clearJob(k);
            ended = true;
            return;
        }
        if (s.jobs !== prev.jobs) refresh();
    });

    const end = () => {
        if (ended) return;
        ended = true;
        unsubscribe();
        const failedSteps = steps().filter(([, j]) => j.status === "error");
        const error = failure ?? (failedSteps.length ? failedSteps.map(([, j]) => `${j.sourceKey}: ${j.error ?? "failed"}`).join("; ") : null);
        // The finished steps go with it; a failed one stays, so its reason is still reachable.
        for (const [k, j] of steps()) if (j.status !== "error") store().clearJob(k);
        const self = store().jobs[key];
        if (!self) return;
        if (error) store().setJob(key, {...self, status: "error", error, stage: "failed"});
        else store().clearJob(key);
    };

    return {
        key,
        trackJob(opts) {
            if (ended) return;
            trackJob({...opts, scopeUrl, parent: key, storeKey: `${key}#${opts.jobId}`});
            refresh();
        },
        hold() {
            holds += 1;
            let released = false;
            return () => {
                if (released) return;
                released = true;
                holds -= 1;
                if (holds <= 0) end();
            };
        },
        stage(text) {
            ownStage = text;
            refresh();
        },
        fail(error) {
            failure = failure ? `${failure}; ${error}` : error;
        },
        progress(fraction) {
            ownProgress = fraction === null ? null : Math.min(1, Math.max(0, fraction));
            refresh();
        },
    };
}
