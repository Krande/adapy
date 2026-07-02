import React, {useEffect, useState} from "react";
import {useConversionStore} from "@/state/conversionStore";
import {useCompressionStore} from "@/state/compressionStore";
import {useMeStore} from "@/state/meStore";
import {useViewerPanelStore} from "@/state/viewerPanelStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {viewerApi} from "@/services/viewerApi";
import {useLoadQueueStore, type LoadTask} from "@/state/loadQueueStore";

// Shared dismiss button. Hit area is 28×28 (Apple HIG min is 44 but
// the toast is dense; 28 still beats the 24 we shipped with and stays
// visually compact). ``pointer-events-auto`` defensively counters any
// ancestor that disables pointer events (the toast container is OK,
// but the surrounding viewer chrome shifts under us so being explicit
// avoids surprise). ``onClick`` stops propagation so a future "click
// on background dismisses" overlay won't swallow the click and so the
// click can't reach the 3D canvas's OrbitControls underneath if the
// stacking ever changes.
const DismissButton: React.FC<{
    onClick: () => void;
    label?: string;
    disabled?: boolean;
}> = ({onClick, label = "Dismiss", disabled = false}) => (
    <button
        type="button"
        onClick={(e) => {
            e.stopPropagation();
            onClick();
        }}
        onMouseDown={(e) => e.stopPropagation()}
        disabled={disabled}
        aria-label={label}
        title={label}
        className={
            "shrink-0 inline-flex items-center justify-center w-7 h-7 rounded-sm " +
            "border border-gray-600 bg-gray-700/60 text-gray-200 cursor-pointer " +
            "pointer-events-auto hover:bg-gray-600 hover:border-gray-400 hover:text-white " +
            "disabled:opacity-50 disabled:cursor-not-allowed"
        }
    >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path
                d="M4 4 L12 12 M12 4 L4 12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                fill="none"
            />
        </svg>
    </button>
);

// (i) Info button — admin-only entry point that opens the Admin panel
// on the Audit Log tab so the operator can dig into the worker-side
// traceback for the failed job. Non-admins don't see it (they have no
// route to that data). Same hit-area + propagation guards as
// DismissButton.
const InfoButton: React.FC<{title?: string}> = ({
    title = "Open audit log for traceback",
}) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    if (!isAdmin) return null;
    return (
        <a
            href="/admin#audit"
            target="_blank"
            rel="noopener"
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
            aria-label={title}
            title={title}
            className={
                "shrink-0 inline-flex items-center justify-center w-7 h-7 rounded-sm " +
                "border border-blue-500/60 bg-blue-700/40 text-blue-200 cursor-pointer " +
                "pointer-events-auto hover:bg-blue-600 hover:border-blue-300 hover:text-white text-xs font-semibold no-underline"
            }
        >
            i
        </a>
    );
};

const STATUS_LABEL: Record<string, string> = {
    queued: "Queued",
    running: "Converting",
    done: "Ready",
    error: "Failed",
};

const CancelButton: React.FC<{
    sourceKey: string;
    jobId: string;
    status: "queued" | "running";
    onCancelled: () => void;
}> = ({sourceKey, jobId, status, onCancelled}) => {
    const [confirming, setConfirming] = useState(false);
    const [busy, setBusy] = useState(false);
    const current = useScopeStore((s) => s.current);

    const verb = status === "queued" ? "Delete" : "Kill";
    const question =
        status === "queued"
            ? "Delete this queued job?"
            : "Kill this running conversion?";

    const doCancel = async () => {
        setBusy(true);
        try {
            if (jobId && current) {
                const scope = scopeUrlPart(current);
                if (scope) {
                    try {
                        await viewerApi.cancelMyJob(scope, jobId);
                    } catch (err) {
                        // Network failure or 5xx — log but still drop
                        // the row from the local store so the user
                        // isn't stuck staring at it.
                        // eslint-disable-next-line no-console
                        console.warn(`[cancel] ${jobId}`, err);
                    }
                }
            }
            // Always clear locally. For stuck-from-crash entries
            // (jobId === ""), the backend row may not exist at all;
            // this just removes the visual artefact.
            onCancelled();
        } finally {
            setBusy(false);
        }
    };

    if (!confirming) {
        return (
            <div className="ml-2">
                <DismissButton
                    onClick={() => setConfirming(true)}
                    label={`${verb} ${sourceKey}`}
                />
            </div>
        );
    }
    return (
        <div className="flex items-center gap-1 ml-2 text-[11px]">
            <span className="text-gray-300">{question}</span>
            <button
                disabled={busy}
                className="bg-red-700 hover:bg-red-600 text-white rounded-sm px-1.5 py-0.5 disabled:opacity-50"
                onClick={doCancel}
            >
                {busy ? "…" : verb}
            </button>
            <button
                disabled={busy}
                className="bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-sm px-1.5 py-0.5 disabled:opacity-50"
                onClick={() => setConfirming(false)}
            >
                No
            </button>
        </div>
    );
};

const ErrorRow: React.FC<{
    sourceKey: string;
    message: string;
    onClear: () => void;
}> = ({sourceKey, message, onClear}) => {
    const [copied, setCopied] = useState(false);

    const onCopy = async () => {
        const payload = `${sourceKey}\n${message}`;
        try {
            await navigator.clipboard.writeText(payload);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — user can still select-and-copy */
        }
    };

    return (
        <div className="flex flex-col gap-1">
            <div className="flex justify-between items-start gap-2">
                <pre className="text-red-400 break-all whitespace-pre-wrap font-mono text-[11px] leading-snug max-h-64 overflow-auto m-0">
                    {message}
                </pre>
                <div className="flex flex-col gap-1 shrink-0">
                    <DismissButton onClick={onClear}/>
                    <InfoButton/>
                </div>
            </div>
            <div className="flex justify-end">
                <button
                    type="button"
                    onClick={onCopy}
                    className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded-sm text-[11px]"
                    title="Copy traceback to clipboard"
                >
                    {copied ? "Copied" : "Copy"}
                </button>
            </div>
        </div>
    );
};

// Compression-sweep toast row — same visual language as the
// conversion toast below; rendered in the same bottom-right slot so
// progress UX stays consistent across "convert one file" and
// "compress all files in a scope" workflows. Stale rows (server
// flagged ``orphaned`` because the pod restarted) get a one-line
// note + a dismiss button so the user can clear them.
const CompressionToast: React.FC<{
    scopeLabel: string;
    state: import("@/services/viewerApi").CompressionSweepState;
    onDismiss: () => void;
}> = ({scopeLabel, state, onDismiss}) => {
    const pct = state.total > 0
        ? Math.round((state.processed / state.total) * 100)
        : 0;
    const finished = state.completed_at !== null;
    const failed = !!state.error || state.errors.length > 0;
    const orphaned = state.orphaned;

    let label: string;
    if (orphaned) {
        label = "Stalled";
    } else if (state.error) {
        label = "Failed";
    } else if (finished) {
        const savedMb = Math.max(
            0, (state.bytes_before - state.bytes_after) / 1024 / 1024,
        );
        label = state.compressed > 0
            ? `Compressed ${state.compressed} (saved ${savedMb.toFixed(0)} MB)`
            : state.already_gzipped > 0
                ? `All ${state.already_gzipped} already gzipped`
                : "Nothing to compress";
    } else {
        label = `Compressing ${state.processed} / ${state.total}`;
    }

    const subtitle = state.current_key || `scope: ${scopeLabel}`;

    return (
        <div className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700">
            <div className="flex justify-between items-center mb-1 gap-2">
                <span className="truncate flex-1" title={subtitle}>
                    {subtitle}
                </span>
                <span className={`ml-2 ${failed || orphaned ? "text-red-400" : "text-gray-400"}`}>
                    {label}
                    {!finished && !orphaned && state.total > 0 && ` ${pct}%`}
                </span>
                {(finished || failed || orphaned) && (
                    <div className="ml-1">
                        <DismissButton onClick={onDismiss}/>
                    </div>
                )}
            </div>
            {!finished && !orphaned && state.total > 0 && (
                <div className="h-1 bg-gray-700 rounded-sm overflow-hidden">
                    <div
                        className="h-full bg-blue-500 transition-all"
                        style={{width: `${Math.max(pct, 4)}%`}}
                    />
                </div>
            )}
            {orphaned && (
                <div className="text-[11px] text-gray-300 mt-1">
                    Viewer restarted mid-sweep. Re-run from Admin → Storage to resume.
                </div>
            )}
            {state.error && (
                <div className="text-[11px] text-red-400 mt-1 break-all">
                    {state.error}
                </div>
            )}
            {state.errors.length > 0 && !state.error && (
                <div className="text-[11px] text-red-400 mt-1">
                    {state.errors.length} file{state.errors.length === 1 ? "" : "s"} failed
                </div>
            )}
        </div>
    );
};

// One-line summary of a job's current step. The worker publishes a
// ``stage`` string alongside each KV update; we surface it directly so
// users see what's actually happening ("loading", "tessellating face
// 1247/4532", "writing output") instead of just a wall-clock spinner.
// Terse codes the worker uses today get a friendlier rendering; any
// free-text the worker publishes is shown verbatim so adding a new
// stage in the worker doesn't require a frontend round trip.
// Friendly translations for the stage codes adapy's converters emit
// (see ada/comms/rest/converter.py — the ``on_progress(stage, frac)``
// callback feeds these in). The map is exhaustive against what's
// emitted today; any unrecognised stage falls through to the raw
// string (so a worker that adds a new stage code can surface it
// without a frontend deploy).
const STAGE_LABEL: Record<string, string> = {
    queued: "Waiting for worker…",
    loading: "Loading source file…",
    parsing: "Parsing source…",
    unpacking: "Unpacking bundle…",
    translating: "Translating geometry…",
    tessellating: "Tessellating geometry…",
    "selecting-field": "Selecting FEA field…",
    writing: "Writing output…",
    "writing-ifc": "Writing IFC…",
    "writing-step": "Writing STEP…",
    "writing-xml": "Writing Genie XML…",
    exporting: "Exporting…",
    convert: "Converting…",
    upload: "Uploading result…",
    uploading: "Uploading result…",
    ready: "Ready",
    cached: "Cached result",
    aborted: "Aborted",
    misrouted: "Routed to wrong pool",
};

function stageText(stage: string | undefined | null): string {
    if (!stage) return "";
    const known = STAGE_LABEL[stage];
    return known || stage;
}

import type {ConversionJob} from "@/state/conversionStore";

// Single in-progress job row inside the unified toast. Pulled out so
// the multi-job expansion can reuse the same shape.
const JobRow: React.FC<{
    job: ConversionJob;
    onCancel: () => void;
    showCancel: boolean;
}> = ({job, onCancel, showCancel}) => {
    const pct = Math.round((job.progress || 0) * 100);
    const isCancellable = job.status === "queued" || job.status === "running";
    return (
        <div className="space-y-1 min-w-0">
            <div className="flex justify-between items-center gap-2 min-w-0">
                <span className="truncate min-w-0 flex-1 text-gray-100" title={job.sourceKey}>
                    {job.sourceKey}
                </span>
                <span className="ml-2 text-gray-400 shrink-0">
                    {STATUS_LABEL[job.status] || job.status} {pct}%
                </span>
                {showCancel && isCancellable && (
                    <CancelButton
                        sourceKey={job.sourceKey}
                        jobId={job.jobId}
                        status={job.status as "queued" | "running"}
                        onCancelled={onCancel}
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
        </div>
    );
};

const basename = (key: string) => key.split("/").pop() ?? key;

// Conversion rows WITHOUT an outer box — the top (newest) job plus a "+N"
// expansion for the rest. Composed inside UnifiedToast so conversion and
// scene-load share one toast box.
const ConversionRows: React.FC<{
    jobs: ConversionJob[];
    onClearJob: (sourceKey: string) => void;
}> = ({jobs, onClearJob}) => {
    const [expanded, setExpanded] = useState(false);
    if (jobs.length === 0) return null;
    // Newest first by startedAt — most-recently kicked-off conversion
    // becomes the "top" job shown in the compact view.
    const sorted = [...jobs].sort((a, b) => b.startedAt - a.startedAt);
    const top = sorted[0];
    const extras = sorted.slice(1);
    const total = jobs.length;

    return (
        <div className="space-y-1">
            <JobRow
                job={top}
                onCancel={() => onClearJob(top.sourceKey)}
                showCancel={true}
            />
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
                                        onCancel={() => onClearJob(j.sourceKey)}
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
const LoadRow: React.FC<{name: string; job?: ConversionJob}> = ({name, job}) => {
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

// Unified activity toast — ONE box for a model's whole lifecycle
// (queue → conversion → upload → load). The scene-load row sits at the top
// as the furthest-along stage; conversion-in-progress rows follow, then the
// load queue and any load errors. Folding the load in here (rather than a
// second LoadQueueToast box) makes the "Loading" counter replace the
// conversion counter in place instead of popping a separate toast.
const UnifiedToast: React.FC<{
    conversionJobs: ConversionJob[];
    onClearJob: (sourceKey: string) => void;
    loadName: string | null;
    loadJob?: ConversionJob;
    loadQueued: LoadTask[];
    loadErrors: Array<{name: string; message: string}>;
    onRemoveQueued: (name: string) => void;
    onClearLoadError: (name: string) => void;
}> = ({
    conversionJobs, onClearJob, loadName, loadJob,
    loadQueued, loadErrors, onRemoveQueued, onClearLoadError,
}) => {
    if (
        !loadName && conversionJobs.length === 0 &&
        loadQueued.length === 0 && loadErrors.length === 0
    ) {
        return null;
    }
    return (
        <div className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700 space-y-1">
            {loadName && <LoadRow name={loadName} job={loadJob}/>}
            {conversionJobs.length > 0 && (
                <div className={loadName ? "pt-1 border-t border-gray-700/60" : ""}>
                    <ConversionRows jobs={conversionJobs} onClearJob={onClearJob}/>
                </div>
            )}
            {loadQueued.length > 0 && (
                <div className="pt-1 border-t border-gray-700/60">
                    <div className="text-[11px] text-gray-400 mb-0.5">
                        Queued ({loadQueued.length})
                    </div>
                    <ul className="space-y-0.5 max-h-40 overflow-auto">
                        {loadQueued.map((t) => (
                            <li key={t.name} className="flex items-center gap-2 min-w-0">
                                <span className="truncate flex-1 min-w-0" title={t.name}>
                                    {basename(t.name)}
                                </span>
                                <button
                                    type="button"
                                    onClick={() => onRemoveQueued(t.name)}
                                    className="shrink-0 px-1 rounded-sm text-gray-400 hover:text-red-300 hover:bg-gray-700 cursor-pointer"
                                    title="Remove from load queue"
                                    aria-label={`Remove ${basename(t.name)} from load queue`}
                                >
                                    ×
                                </button>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
            {loadErrors.map((e) => (
                <div
                    key={e.name}
                    className="flex items-start justify-between gap-2 pt-1 border-t border-gray-700/60"
                >
                    <div className="min-w-0">
                        <div className="truncate" title={e.name}>{basename(e.name)}</div>
                        <div className="text-[11px] text-red-400 break-all">{e.message}</div>
                    </div>
                    <button
                        type="button"
                        onClick={() => onClearLoadError(e.name)}
                        className="shrink-0 px-1 rounded-sm text-gray-400 hover:text-white hover:bg-gray-700 cursor-pointer"
                        title="Dismiss"
                        aria-label={`Dismiss error for ${basename(e.name)}`}
                    >
                        ×
                    </button>
                </div>
            ))}
        </div>
    );
};

// Ambient indicator for in-progress audit sweeps. Admin-only — the
// endpoint 403s for non-admins. Polls /admin/audit/active every 15s
// so the badge stays current without re-fetching on every render.
// Click → /admin#audit_runs (full audit panel). Hidden whenever
// running_runs == 0 so the slot frees up for the conversion toast
// the moment sweeps drain.
type AuditActiveSummary = {
    running_runs: number;
    pending_cells: number;
    current_cell: {
        key: string | null;
        target_format: string | null;
        status: string | null;
        started_at: string | null;
        elapsed_ms: number | null;
    } | null;
};

function fmtCellElapsed(ms: number | null): string {
    if (ms == null) return "";
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60_000) return `${Math.round(ms / 1000)} s`;
    return `${Math.round(ms / 60_000)} m`;
}

const AuditActivityBadge: React.FC = () => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const openPanel = useViewerPanelStore((s) => s.openPanel);
    const [summary, setSummary] = useState<AuditActiveSummary | null>(null);

    useEffect(() => {
        if (!isAdmin) return;
        let cancelled = false;
        const poll = async () => {
            try {
                const r = await viewerApi.adminAuditActive();
                if (!cancelled) setSummary(r);
            } catch {
                // Network blip or auth dropped — silently retry on
                // the next tick. The badge stays at its last
                // known state in the meantime.
            }
        };
        void poll();
        // 2s while an audit is running so the "now: …" line keeps
        // up with actual cell turnover (cells flip every couple of
        // seconds on a force-rebuild). When nothing's running we
        // revert to the cheap 15s cadence — ``summary`` re-mounts
        // the effect implicitly because ``running_runs`` only flips
        // on the next poll, so this is a one-knob compromise.
        const interval = summary && summary.running_runs > 0 ? 2_000 : 15_000;
        const id = window.setInterval(poll, interval);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, [isAdmin, summary && summary.running_runs > 0]);

    if (!isAdmin || !summary || summary.running_runs === 0) return null;
    const {running_runs, pending_cells, current_cell} = summary;
    return (
        <button
            type="button"
            onClick={() => openPanel("admin", "audit_runs")}
            className={
                "block w-full text-left bg-blue-950/80 hover:bg-blue-900 border border-blue-700 " +
                "text-blue-100 rounded-sm shadow-lg px-3 py-2 text-xs no-underline " +
                "pointer-events-auto cursor-pointer"
            }
            title="Open Audit Runs in the panel"
        >
            <div className="flex items-center justify-between gap-2 min-w-0">
                <span className="font-medium truncate">
                    {running_runs === 1
                        ? "Audit sweep in progress"
                        : `${running_runs} audit sweeps in progress`}
                </span>
                <span className="text-blue-300 shrink-0">→</span>
            </div>
            {pending_cells > 0 && (
                <div className="text-[11px] text-blue-300 mt-0.5">
                    {pending_cells} cell{pending_cells === 1 ? "" : "s"} pending
                </div>
            )}
            {current_cell && current_cell.key && current_cell.target_format
                && current_cell.status === "running" && (
                <div className="text-[11px] text-blue-200 mt-1 min-w-0">
                    <span className="text-blue-400">now: </span>
                    <span className="font-mono truncate inline-block max-w-full align-bottom" title={current_cell.key}>
                        {current_cell.key} → {current_cell.target_format}
                    </span>
                    {current_cell.elapsed_ms != null && (
                        <span className="text-blue-400 ml-1">
                            · {fmtCellElapsed(current_cell.elapsed_ms)}
                        </span>
                    )}
                </div>
            )}
        </button>
    );
};

const ConversionProgress = () => {
    const jobs = useConversionStore((s) => s.jobs);
    const clearJob = useConversionStore((s) => s.clearJob);
    const sweeps = useCompressionStore((s) => s.sweeps);
    const clearSweep = useCompressionStore((s) => s.clearSweep);
    const loadCurrent = useLoadQueueStore((s) => s.current);
    const loadQueued = useLoadQueueStore((s) => s.queued);
    const loadErrors = useLoadQueueStore((s) => s.errors);
    const removeQueued = useLoadQueueStore((s) => s.removeQueued);
    const clearError = useLoadQueueStore((s) => s.clearError);
    const loadCurrentName = loadCurrent?.name ?? null;

    // The in-scene GLB download/parse is tracked as a conversionStore job under a
    // sentinel key (asyncModelLoader's LOAD_KEY). It's the SAME activity as the
    // scene load, so it must feed the single load row — not show as its own
    // conversion row (that's the "two toasts for one load" the merge was meant
    // to kill). Direct loadGLTF calls (no load-queue entry) set it without a
    // loadCurrent, so the load row falls back to this job's own name.
    const MODEL_LOAD_KEY = "model-load";
    const modelLoadJob = jobs[MODEL_LOAD_KEY];
    const modelLoadActive =
        !!modelLoadJob && (modelLoadJob.status === "queued" || modelLoadJob.status === "running");

    const loadName = loadCurrentName ?? (modelLoadActive ? modelLoadJob.derivedKey || "model" : null);
    const loadQueueActive =
        loadName !== null || loadQueued.length > 0 || loadErrors.length > 0;

    // In-progress jobs collapse into one toast; errors stay split so each one's
    // traceback + copy button is reachable. Excluded from the conversion rows:
    // (a) the conversion/bake job driving the current scene load, and (b) the
    // model-load GLB-download job — both surface on the single load row.
    const inProgress = Object.entries(jobs)
        .filter(([k, j]) =>
            (j.status === "queued" || j.status === "running") &&
            k !== MODEL_LOAD_KEY &&
            !(loadCurrentName && k.startsWith(loadCurrentName + "::")))
        .map(([, j]) => j);
    // Load-row progress: the conversion/bake job still feeding the load if one is
    // running, else the GLB download job — so the bar runs queue→convert→upload→load.
    const loadJob =
        (loadCurrentName
            ? Object.entries(jobs).find(([k]) => k.startsWith(loadCurrentName + "::"))?.[1]
            : undefined) ?? (modelLoadActive ? modelLoadJob : undefined);
    const errored = Object.values(jobs).filter((j) => j.status === "error" && j.sourceKey !== MODEL_LOAD_KEY);
    const allVisible = [...inProgress, ...errored];
    const visibleSweeps = Object.entries(sweeps);

    // We always render the outer slot so the admin-only
    // AuditActivityBadge has somewhere to mount — the badge polls
    // internally and returns null whenever no audits are running,
    // so an empty container is invisible. The toast + sweeps only
    // render when there's actually something to show.
    const isAdmin = useMeStore.getState().isAdmin;
    if (allVisible.length === 0 && visibleSweeps.length === 0 && !loadQueueActive && !isAdmin) {
        return null;
    }

    return (
        // ``pointer-events-auto`` is explicit so the toast row always
        // receives clicks even if a future ancestor opts into
        // pointer-events-none for the chrome layer (some overlay
        // shells do that to let drags reach the canvas).
        //
        // Mobile width: anchor to both left and right (16px from each
        // edge) so a long source key can't overflow the viewport.
        // From the ``sm:`` breakpoint up we revert to the desktop
        // floating-pill style — anchored to the right, capped at
        // ``max-w-sm`` (24rem).
        <div className="absolute bottom-4 left-4 right-4 sm:left-auto sm:max-w-sm z-50 flex flex-col gap-2 pointer-events-auto">
            <AuditActivityBadge/>
            {visibleSweeps.map(([scopeLabel, state]) => (
                <CompressionToast
                    key={`compress:${scopeLabel}`}
                    scopeLabel={scopeLabel}
                    state={state}
                    onDismiss={() => clearSweep(scopeLabel)}
                />
            ))}
            <UnifiedToast
                conversionJobs={inProgress}
                onClearJob={clearJob}
                loadName={loadName}
                loadJob={loadJob}
                loadQueued={loadQueued}
                loadErrors={loadErrors}
                onRemoveQueued={removeQueued}
                onClearLoadError={clearError}
            />
            {errored.map((job) => (
                <div
                    key={job.sourceKey}
                    className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700"
                >
                    <div className="flex justify-between items-center mb-1">
                        <span className="truncate flex-1">{job.sourceKey}</span>
                        <span className="ml-2 text-red-400 shrink-0">
                            {STATUS_LABEL[job.status] || job.status}
                        </span>
                    </div>
                    <ErrorRow
                        sourceKey={job.sourceKey}
                        message={job.error || "(no error message)"}
                        onClear={() => clearJob(job.sourceKey)}
                    />
                </div>
            ))}
        </div>
    );
};

export default ConversionProgress;
