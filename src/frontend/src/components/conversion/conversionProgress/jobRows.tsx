// The rows of the progress toast: a conversion or operation job (with its run actions), the
// list of them, and the scene-load row.

import React, { useState } from "react";
import { cancelOperation } from "@/services/operationTracking";
import ExtendJobButton from "../ExtendJobButton";
import { stageText } from "../stageText";
import { hasRunView, parentRunOf, requeueSweepItems, sweepRequeueTargets, toastSkipAction, viewToastRun, workerLabel } from "@/services/sweepTracking";
import type { ConversionJob } from "@/state/conversionStore";
import { CancelButton } from "./buttons";

export const STATUS_LABEL: Record<string, string> = {
    queued: "Queued",
    running: "Converting",
    done: "Ready",
    error: "Failed",
};

// Single in-progress job row inside the unified toast. Pulled out so
// the multi-job expansion can reuse the same shape.
// A watched sweep or export run (`sweepTracking.ts`) is not a job: its dismiss only stops watching.
// What it offers instead is "Skip <collection>" -- give up on the item that is hanging and carry
// on -- and "View run", which opens the table that shows the run, expanded on it. Read on every
// render (the entry is re-set on every poll, so the row re-renders with the run's latest read).
export const RunActions: React.FC<{storeKey: string}> = ({storeKey}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [requeueOpen, setRequeueOpen] = useState(false);
    const skip = toastSkipAction(storeKey);
    const canView = hasRunView(storeKey);
    const failed = sweepRequeueTargets(storeKey);
    if (!skip && !canView && failed.length === 0) return null;
    const btn = "bg-gray-700 hover:bg-gray-600 text-gray-100 px-1.5 py-0.5 rounded-sm text-[11px] disabled:opacity-50";
    const requeue = (positions: number[]) => {
        setBusy(true);
        setErr(null);
        requeueSweepItems(storeKey, positions)
            .then(() => setRequeueOpen(false))
            .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
            .finally(() => setBusy(false));
    };
    return (
        <div className="flex flex-wrap items-center gap-1">
            {failed.length > 0 && (
                <button
                    type="button"
                    className={btn}
                    disabled={busy}
                    title="Run failed collections of this sweep again"
                    onClick={() => setRequeueOpen(!requeueOpen)}
                >
                    Re-queue failed ({failed.length}) {requeueOpen ? "▾" : "▸"}
                </button>
            )}
            {requeueOpen && failed.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 w-full max-h-24 overflow-auto">
                    {failed.length > 1 && (
                        <button type="button" className={btn} disabled={busy} onClick={() => requeue(failed.map((f) => f.position))}>
                            All
                        </button>
                    )}
                    {failed.map((f) => (
                        <button
                            key={f.position}
                            type="button"
                            className={btn}
                            disabled={busy}
                            title={`Run ${f.collection} again`}
                            onClick={() => requeue([f.position])}
                        >
                            {f.collection}
                        </button>
                    ))}
                </div>
            )}
            {canView && (
                <button type="button" className={btn} title="Open the run's table" onClick={() => viewToastRun(storeKey)}>
                    View run
                </button>
            )}
            {skip && (
                <button
                    type="button"
                    className={btn}
                    disabled={busy || skip.pending}
                    title="Give up on this item and carry on with the rest (the run keeps going)"
                    onClick={() => {
                        setBusy(true);
                        setErr(null);
                        skip.run()
                            .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
                            .finally(() => setBusy(false));
                    }}
                >
                    {busy ? "…" : skip.label}
                </button>
            )}
            {err && <span className="text-[11px] text-red-400 break-all">{err}</span>}
        </div>
    );
};

const JobRow: React.FC<{
    job: ConversionJob;
    onCancel: () => void;
    showCancel: boolean;
    storeKey?: string;
}> = ({job, onCancel, showCancel, storeKey}) => {
    const pct = Math.round((job.progress || 0) * 100);
    const isCancellable = job.status === "queued" || job.status === "running";
    const isRun =
        !!storeKey && (storeKey.startsWith("sweep:") || storeKey.startsWith("export:")) && !storeKey.includes("#");
    const isOperation = !!storeKey && isOperationKey(storeKey);
    // A watched run's worker job is named by the item it runs, not by its internal source key.
    const label = workerLabel(job.jobId) ?? job.sourceKey;
    return (
        <div className="space-y-1 min-w-0">
            <div className="flex justify-between items-center gap-2 min-w-0">
                <span className="truncate min-w-0 flex-1 text-gray-100" title={job.sourceKey}>
                    {label}
                </span>
                <span className="ml-2 text-gray-400 shrink-0">
                    {STATUS_LABEL[job.status] || job.status} {pct}%
                </span>
                {showCancel && job.extendable && job.status === "running" && !!job.jobId && (
                    <ExtendJobButton jobId={job.jobId} label={job.sourceKey}/>
                )}
                {showCancel && isCancellable && (
                    <CancelButton
                        sourceKey={job.sourceKey}
                        jobId={job.jobId}
                        status={job.status as "queued" | "running"}
                        onCancelled={() => {
                            // Kill on an operation stops it for real; an entry it no longer
                            // drives (one left from before a reload) is just cleared.
                            if (!(isOperation && cancelOperation(storeKey!))) onCancel();
                        }}
                        operation={isOperation}
                    />
                )}
            </div>
            {stageText(job.stage) && (
                <div className="text-[11px] text-gray-400 truncate" title={stageText(job.stage)}>
                    {stageText(job.stage)}
                </div>
            )}
            <div className="h-1 bg-gray-700 rounded-sm overflow-hidden">
                <div
                    className="h-full bg-blue-500 transition-all"
                    style={{width: `${Math.max(pct, 4)}%`}}
                />
            </div>
            {isRun && <RunActions storeKey={storeKey!}/>}
        </div>
    );
};

export const basename = (key: string) => key.split("/").pop() ?? key;

/** An operation's own entry (`op:<id>`), not one of its steps (`op:<id>#<job>`). */
const isOperationKey = (key: string) => key.startsWith("op:") && !key.includes("#");

// Conversion rows WITHOUT an outer box — the top (newest) job plus a "+N"
// expansion for the rest. Composed inside UnifiedToast so conversion and
// scene-load share one toast box.
/** A job plus the key it is stored under. The two differ (`sourceKey` vs
 *  `${sourceKey}::${target}`) and only the latter can address the entry. */
export type ToastJob = ConversionJob & {storeKey: string};

export const ConversionRows: React.FC<{
    jobs: ToastJob[];
    onClearJob: (storeKey: string) => void;
}> = ({jobs, onClearJob}) => {
    const [expanded, setExpanded] = useState(false);
    const [workersOpen, setWorkersOpen] = useState(false);
    const [workerAt, setWorkerAt] = useState(0);
    if (jobs.length === 0) return null;
    // Newest first by startedAt — most-recently kicked-off conversion
    // becomes the "top" job shown in the compact view. A watched sweep or export
    // RUN always comes first, whatever its age: it is the one row with actions
    // (View run, Skip), and its own parallel jobs -- newer by construction --
    // would otherwise push it into the collapsed "+N more".
    // A bounds or attributes request and an operation are runs too (their steps are worker rows), without the
    // runs-list actions.
    // `<run key>#<position>` is one of the run's worker entries, not a run.
    const isRunKey = (k: string) =>
        (k.startsWith("sweep:") ||
            k.startsWith("export:") ||
            k.startsWith("bounds:") ||
            k.startsWith("attrs:") ||
            k.startsWith("op:")) &&
        !k.includes("#");
    const sorted = [...jobs].sort(
        (a, b) => Number(isRunKey(b.storeKey)) - Number(isRunKey(a.storeKey)) || b.startedAt - a.startedAt,
    );
    const top = sorted[0];
    const shown = new Set(jobs.map((j) => j.storeKey));
    // The top run's own jobs are its WORKERS, not more conversions beside it: shown under it, one
    // at a time or all, and left out of the "+N more" count (which would count the run twice).
    const workers = isRunKey(top.storeKey)
        ? sorted
              .slice(1)
              .filter((j) => j.parent === top.storeKey || parentRunOf(j.jobId) === top.storeKey)
              .sort((a, b) => a.startedAt - b.startedAt)
        : [];
    // Another operation's steps belong to it, not to this list.
    const extras = sorted.slice(1).filter((j) => !workers.includes(j) && !(j.parent && shown.has(j.parent)));
    const total = 1 + extras.length;
    const at = Math.min(workerAt, Math.max(0, workers.length - 1));
    const pager = "px-1 rounded-sm text-gray-400 hover:text-gray-100 hover:bg-gray-700 disabled:opacity-30";
    const workerRow = (j: ToastJob) => (
        <JobRow job={j} storeKey={j.storeKey} onCancel={() => onClearJob(j.storeKey)} showCancel={true}/>
    );

    return (
        <div className="space-y-1">
            <JobRow
                job={top}
                storeKey={top.storeKey}
                onCancel={() => onClearJob(top.storeKey)}
                showCancel={true}
            />
            {workers.length > 0 && (
                <div className="pt-1 border-t border-gray-700/60 space-y-1">
                    {workersOpen ? (
                        <div className="space-y-2 max-h-64 overflow-auto">
                            {workers.map((j) => (
                                <div key={j.storeKey}>{workerRow(j)}</div>
                            ))}
                        </div>
                    ) : (
                        <div className="flex items-start gap-1 min-w-0">
                            {workers.length > 1 && (
                                <div className="flex items-center shrink-0 pt-0.5">
                                    <button type="button" className={pager} disabled={at === 0} aria-label="Previous worker" onClick={() => setWorkerAt(at - 1)}>
                                        ‹
                                    </button>
                                    <span className="text-[10px] text-gray-500 tabular-nums">
                                        {at + 1}/{workers.length}
                                    </span>
                                    <button
                                        type="button"
                                        className={pager}
                                        disabled={at === workers.length - 1}
                                        aria-label="Next worker"
                                        onClick={() => setWorkerAt(at + 1)}
                                    >
                                        ›
                                    </button>
                                </div>
                            )}
                            <div className="min-w-0 flex-1">{workerRow(workers[at])}</div>
                        </div>
                    )}
                    {workers.length > 1 && (
                        <button
                            type="button"
                            onClick={() => setWorkersOpen(!workersOpen)}
                            className="text-[11px] text-blue-300 hover:text-blue-200 flex items-center gap-1"
                            title={workersOpen ? "Show one worker at a time" : "Show every active worker"}
                        >
                            <span className="font-mono bg-blue-900/40 border border-blue-700 px-1.5 py-0.5 rounded-sm">
                                {workersOpen ? workers.length : `+${workers.length - 1}`}
                            </span>
                            <span>
                                {workersOpen
                                    ? "active workers"
                                    : `more active ${workers.length - 1 === 1 ? "worker" : "workers"}`}
                            </span>
                            <span className="ml-1">{workersOpen ? "▾" : "▸"}</span>
                        </button>
                    )}
                </div>
            )}
            {extras.length > 0 && (
                <div className="pt-1 border-t border-gray-700/60">
                    <button
                        type="button"
                        onClick={() => setExpanded(!expanded)}
                        className="text-[11px] text-blue-300 hover:text-blue-200 flex items-center gap-1"
                        title={expanded ? "Hide other jobs" : "Show other jobs"}
                    >
                        <span className="font-mono bg-blue-900/40 border border-blue-700 px-1.5 py-0.5 rounded-sm">
                            +{extras.length}
                        </span>
                        <span>
                            {extras.length === 1 ? "more conversion" : "more conversions"}
                            {" "}({total} total)
                        </span>
                        <span className="ml-1">{expanded ? "▾" : "▸"}</span>
                    </button>
                    {expanded && (
                        <div className="mt-2 space-y-2 max-h-64 overflow-auto">
                            {extras.map((j) => (
                                <div
                                    key={j.sourceKey}
                                    className="border-t border-gray-700/40 pt-2"
                                >
                                    <JobRow
                                        job={j}
                                        storeKey={j.storeKey}
                                        onCancel={() => onClearJob(j.storeKey)}
                                        showCancel={true}
                                    />
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

// Scene-load row WITHOUT an outer box — the *continuation* of one model's
// lifecycle after queue→convert→upload: the same toast now reads "Loading".
// While the load is still waiting on its conversion/bake job we surface that
// job's % + stage (the bar keeps moving); once that's done the GLB
// download/parse has no server-side counter, so an indeterminate bar.
export const LoadRow: React.FC<{name: string; job?: ConversionJob}> = ({name, job}) => {
    const pct = job ? Math.round((job.progress || 0) * 100) : null;
    const stage = stageText(job?.stage);
    return (
        <div className="space-y-1 min-w-0">
            <div className="flex justify-between items-center gap-2 min-w-0">
                <span className="truncate min-w-0 flex-1 text-gray-100" title={name}>
                    {basename(name)}
                </span>
                <span className="ml-2 text-gray-400 shrink-0">
                    Loading{pct !== null ? ` ${pct}%` : "…"}
                </span>
            </div>
            {stage && (
                <div className="text-[11px] text-gray-400 truncate" title={stage}>
                    {stage}
                </div>
            )}
            <div className="h-1 bg-gray-700 rounded-sm overflow-hidden">
                {pct !== null ? (
                    <div
                        className="h-full bg-blue-500 transition-all"
                        style={{width: `${Math.max(pct, 4)}%`}}
                    />
                ) : (
                    <div className="h-full w-1/3 bg-blue-500 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                )}
            </div>
        </div>
    );
};
