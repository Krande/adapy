import React, {useState} from "react";
import {useAdminPanelStore} from "@/state/adminPanelStore";
import {useConversionStore} from "@/state/conversionStore";
import {useCompressionStore} from "@/state/compressionStore";
import {useMeStore} from "@/state/meStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {viewerApi} from "@/services/viewerApi";

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
    const openAdmin = useAdminPanelStore((s) => s.openAdmin);
    if (!isAdmin) return null;
    return (
        <button
            type="button"
            onClick={(e) => {
                e.stopPropagation();
                openAdmin("audit");
            }}
            onMouseDown={(e) => e.stopPropagation()}
            aria-label={title}
            title={title}
            className={
                "shrink-0 inline-flex items-center justify-center w-7 h-7 rounded-sm " +
                "border border-blue-500/60 bg-blue-700/40 text-blue-200 cursor-pointer " +
                "pointer-events-auto hover:bg-blue-600 hover:border-blue-300 hover:text-white text-xs font-semibold"
            }
        >
            i
        </button>
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

const ConversionProgress = () => {
    const jobs = useConversionStore((s) => s.jobs);
    const clearJob = useConversionStore((s) => s.clearJob);
    const sweeps = useCompressionStore((s) => s.sweeps);
    const clearSweep = useCompressionStore((s) => s.clearSweep);

    const visibleJobs = Object.values(jobs).filter(
        (j) => j.status === "queued" || j.status === "running" || j.status === "error"
    );
    const visibleSweeps = Object.entries(sweeps);

    if (visibleJobs.length === 0 && visibleSweeps.length === 0) {
        return null;
    }

    return (
        // ``pointer-events-auto`` is explicit so the toast row always
        // receives clicks even if a future ancestor opts into
        // pointer-events-none for the chrome layer (some overlay
        // shells do that to let drags reach the canvas).
        <div className="absolute bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm pointer-events-auto">
            {visibleSweeps.map(([scopeLabel, state]) => (
                <CompressionToast
                    key={`compress:${scopeLabel}`}
                    scopeLabel={scopeLabel}
                    state={state}
                    onDismiss={() => clearSweep(scopeLabel)}
                />
            ))}
            {visibleJobs.map((job) => {
                const pct = Math.round((job.progress || 0) * 100);
                const isError = job.status === "error";
                const isCancellable = job.status === "queued" || job.status === "running";
                return (
                    <div
                        key={job.sourceKey}
                        className="bg-gray-800 text-gray-100 rounded-sm shadow-lg px-3 py-2 text-xs border border-gray-700"
                    >
                        <div className="flex justify-between items-center mb-1">
                            <span className="truncate flex-1">{job.sourceKey}</span>
                            <span className="ml-2 text-gray-400">
                                {STATUS_LABEL[job.status] || job.status}
                                {!isError && ` ${pct}%`}
                            </span>
                            {isCancellable && (
                                <CancelButton
                                    sourceKey={job.sourceKey}
                                    jobId={job.jobId}
                                    status={job.status as "queued" | "running"}
                                    onCancelled={() => clearJob(job.sourceKey)}
                                />
                            )}
                        </div>
                        {!isError && (
                            <div className="h-1 bg-gray-700 rounded-sm overflow-hidden">
                                <div
                                    className="h-full bg-blue-500 transition-all"
                                    style={{width: `${Math.max(pct, 4)}%`}}
                                />
                            </div>
                        )}
                        {isError && (
                            <ErrorRow
                                sourceKey={job.sourceKey}
                                message={job.error || "(no error message)"}
                                onClear={() => clearJob(job.sourceKey)}
                            />
                        )}
                    </div>
                );
            })}
        </div>
    );
};

export default ConversionProgress;
