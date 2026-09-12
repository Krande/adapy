import React, {useEffect, useState} from "react";
import {ApiError, AuditEntry, viewerApi} from "@/services/viewerApi";
import {useAuditFilterStore} from "@/state/auditFilterStore";
import ClientMetricsTab from "./ClientMetricsTab";
import MetricsTab from "./MetricsTab";
import {formatDuration, hasMetrics, isClientMetricsRow} from "./format";

// Lazy-loaded conversion log (worker-captured stdout/stderr, stored at log_key). Fetched on first
// view of the Log tab — surfaces what the structured fields don't, e.g. an "adacpp-native ...
// falling back to libtess2" warning when a pipeline silently degrades inside the convert subprocess.
const LogTab: React.FC<{entry: AuditEntry}> = ({entry}) => {
    const [text, setText] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        setErr(null);
        viewerApi
            .adminGetAuditLog(entry.id)
            .then((t) => {
                if (alive) setText(t);
            })
            .catch((e) => {
                if (!alive) return;
                setErr(
                    e instanceof ApiError
                        ? e.status === 404
                            ? "No log captured for this row."
                            : e.detail || e.message
                        : String(e),
                );
            })
            .finally(() => {
                if (alive) setLoading(false);
            });
        return () => {
            alive = false;
        };
    }, [entry.id]);

    if (loading) return <div className="p-4 text-sm text-gray-400">Loading log…</div>;
    if (err) return <div className="p-4 text-sm text-gray-400 italic">{err}</div>;
    return (
        <pre className="p-3 text-xs leading-relaxed whitespace-pre-wrap break-words text-gray-200 font-mono">
            {text}
        </pre>
    );
};

// Tabbed details view: Error (or 'OK' summary) on one tab, Metrics
// on the other. Both tabs render even when their data is partial so
// the user gets a consistent layout regardless of job outcome — a
// timed-out conversion and a clean success share the same shape.
const DetailsModal: React.FC<{entry: AuditEntry; onClose: () => void}> = ({entry, onClose}) => {
    const clientRow = isClientMetricsRow(entry);
    const [tab, setTab] = useState<"error" | "metrics" | "client" | "log">(
        entry.error || entry.traceback ? "error" : clientRow ? "client" : "metrics",
    );
    const [copied, setCopied] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const [downloadErr, setDownloadErr] = useState<string | null>(null);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    const traceText = entry.traceback || entry.error || "";
    const copyPayload =
        (entry.error ? `${entry.error}\n\n` : "") + (entry.traceback || "");

    const onCopy = async () => {
        try {
            await navigator.clipboard.writeText(copyPayload || traceText);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — user can still select-and-copy by hand */
        }
    };

    const onDownloadProfile = async () => {
        if (!entry.profile_key) return;
        setDownloading(true);
        setDownloadErr(null);
        try {
            // Suggest the storage filename so the user gets a stable
            // name on disk; tail of the key after the last slash.
            const suggested = entry.profile_key.split("/").pop() || `audit-${entry.id}.prof`;
            await viewerApi.adminDownloadProfile(entry.id, suggested);
        } catch (e) {
            setDownloadErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setDownloading(false);
        }
    };

    return (
        <div
            className="fixed inset-0 z-60 flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
            onClick={onClose}
        >
            <div
                className="bg-gray-900 border border-gray-700 rounded-sm shadow-xl flex flex-col max-w-3xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="Audit row details"
            >
                <div className="flex items-start gap-3 border-b border-gray-700 px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">Audit details</div>
                        <div className="text-xs text-gray-400 truncate" title={entry.key || ""}>
                            {entry.action}
                            {entry.key ? ` · ${entry.key}` : ""}
                            {entry.target_format ? ` → ${entry.target_format}` : ""}
                        </div>
                    </div>
                    {/* In the HEADER, not on a tab. It is an action on the job,
                        not part of any one view of it — and a tab called
                        "Outcome" is the last place someone looks for a button
                        that changes the outcome. Renders nothing unless the row
                        is actually cancellable, so it costs no space on the
                        overwhelming majority of rows, which are history. */}
                    <StuckJobActions entry={entry}/>
                    {tab === "error" && entry.traceback && (
                        <button
                            type="button"
                            className="shrink-0 bg-gray-800 hover:bg-gray-700 text-gray-100 px-2 py-1 rounded-sm text-xs"
                            onClick={onCopy}
                            title="Copy traceback to clipboard"
                        >
                            {copied ? "Copied" : "Copy"}
                        </button>
                    )}
                    <button
                        type="button"
                        className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2"
                        onClick={onClose}
                        aria-label="Close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>
                <div className="flex border-b border-gray-700 text-xs">
                    {clientRow && (
                        <TabButton active={tab === "client"} onClick={() => setTab("client")}>
                            {entry.action === "render" ? "Render" : "Load"}
                        </TabButton>
                    )}
                    <TabButton active={tab === "error"} onClick={() => setTab("error")}>
                        {entry.error ? "Error" : "Outcome"}
                    </TabButton>
                    {!clientRow && (
                        <TabButton active={tab === "metrics"} onClick={() => setTab("metrics")}>
                            Metrics
                            {hasMetrics(entry) ? null : (
                                <span className="text-gray-500"> (none)</span>
                            )}
                        </TabButton>
                    )}
                    {!clientRow && (
                        <TabButton active={tab === "log"} onClick={() => setTab("log")}>
                            Log
                        </TabButton>
                    )}
                </div>
                <div className="flex-1 overflow-auto">
                    {tab === "error" && (
                        <ErrorTab entry={entry}/>
                    )}
                    {tab === "metrics" && (
                        <MetricsTab
                            entry={entry}
                            onDownloadProfile={onDownloadProfile}
                            downloading={downloading}
                            downloadErr={downloadErr}
                        />
                    )}
                    {tab === "client" && <ClientMetricsTab entry={entry}/>}
                    {tab === "log" && <LogTab entry={entry}/>}
                </div>
            </div>
        </div>
    );
};

const TabButton: React.FC<{active: boolean; onClick: () => void; children: React.ReactNode}> = ({
    active, onClick, children,
}) => (
    <button
        type="button"
        onClick={onClick}
        className={
            "px-3 py-1.5 border-b-2 " +
            (active
                ? "border-blue-500 text-white"
                : "border-transparent text-gray-400 hover:text-gray-200")
        }
    >
        {children}
    </button>
);

const ErrorTab: React.FC<{entry: AuditEntry}> = ({entry}) => {
    if (entry.error || entry.traceback) {
        return (
            <>
                {entry.error && (
                    <div className="px-4 py-2 text-sm text-red-300 border-b border-gray-800 wrap-break-word">
                        {entry.error}
                    </div>
                )}
                {entry.traceback ? (
                    <pre className="px-4 py-2 text-xs text-gray-200 whitespace-pre font-mono">
{entry.traceback}
                    </pre>
                ) : (
                    <div className="px-4 py-3 text-xs text-gray-400">
                        No traceback recorded for this entry.
                    </div>
                )}
            </>
        );
    }
    return (
        <div className="px-4 py-3 text-xs text-gray-300 space-y-1">
            <div>Status: <span className="font-mono">{entry.status || "n/a"}</span></div>
            {entry.duration_ms != null && (
                <div>Duration: <span className="font-mono">{formatDuration(entry.duration_ms)}</span></div>
            )}
            {entry.job_id && (
                <div className="break-all">Job: <span className="font-mono">{entry.job_id}</span></div>
            )}
            <QueueRouting entry={entry}/>
            <div className="text-gray-500 mt-2">
                No error reported for this entry. Switch to the Metrics tab for
                CPU / memory / IO data.
            </div>
        </div>
    );
};

// Which pool a still-pending job is waiting on.
//
// THE ONE FACT THAT EXPLAINS A STUCK JOB, and it was not on screen anywhere. A job
// routed to a pool no worker subscribes to is accepted and then never delivered:
// nothing pulls it, so it is never redelivered, so it never reaches the
// delivery-attempt cap that would record an error. The row stays `queued` with no
// error, no retry, and no line in any worker's log — and every visible field looks
// normal. Reading the pool off the queue entry is how that gets diagnosed in one
// look instead of by reading queue source.
//
// Only for a non-terminal row: for history the pool is a spent detail, and the
// queue entry has usually been swept anyway, which would make this a 404 on most
// rows an operator opens.
const QueueRouting: React.FC<{entry: AuditEntry}> = ({entry}) => {
    const [pool, setPool] = useState<string | null | undefined>(undefined);
    const [gone, setGone] = useState(false);
    const status = (entry.status || "").toLowerCase();
    const pending = !!entry.job_id && (status === "queued" || status === "running");

    useEffect(() => {
        if (!pending || !entry.job_id) return;
        let cancelled = false;
        (async () => {
            try {
                const job = await viewerApi.convertStatus(entry.job_id!);
                if (!cancelled) setPool(job.target_capability ?? null);
            } catch {
                // A 404 means the entry is no longer in the queue at all, which is
                // itself the answer for a row still claiming to be queued: there
                // is nothing left to deliver. Not an error worth a red box.
                if (!cancelled) setGone(true);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [pending, entry.job_id]);

    if (!pending) return null;
    if (gone) {
        return (
            <div className="text-amber-300">
                No queue entry for this job — nothing is left to deliver, so this row will not
                move on its own. Cancel it to clear the status.
            </div>
        );
    }
    if (pool === undefined) return <div className="text-gray-500">Pool: looking up…</div>;
    return (
        <div>
            Pool: <span className="font-mono">{pool || "base"}</span>
            {!pool && (
                <span className="text-amber-300">
                    {" "}— routed to the default pool, which a specialised worker does not subscribe to
                </span>
            )}
        </div>
    );
};

// Clearing a job that nothing is ever going to finish.
//
// Shown only for a NON-TERMINAL entry with a job id. A done or error row has
// nothing to cancel, and putting the button there would invite an operator to
// "fix" a row that is simply history.
//
// The job this is for is one queued against a capability no live worker serves
// — a retired pool, a renamed capability, a worker that never came back. It is
// never pulled, so it never reaches a terminal status, so the KV sweep (which
// only touches terminal entries) never clears it: it shows as pending forever.
// The user-facing cancel cannot reach it either, because that one filters on
// the job's owner and an operator cleaning up after a pool is not that person.
const StuckJobActions: React.FC<{entry: AuditEntry}> = ({entry}) => {
    const [busy, setBusy] = useState(false);
    const [done, setDone] = useState<string | null>(null);
    const [err, setErr] = useState<string | null>(null);

    const status = (entry.status || "").toLowerCase();
    if (!entry.job_id || (status !== "queued" && status !== "running")) return null;

    const onCancel = async () => {
        if (
            !confirm(
                `Cancel job ${entry.job_id}?\n\n` +
                "The audit row is marked cancelled and the queue entry is dropped. " +
                "A worker that picks the message up later will see the cancellation " +
                "and drop it. If the job is running right now it stops at its next " +
                "cancellation check, and anything it already wrote stays written.",
            )
        ) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            const r = await viewerApi.adminCancelJob(entry.job_id!);
            // Both halves reported: a job can be stuck in the audit row, in the
            // queue entry, or in both, and "cancelled" alone would leave an
            // operator unsure whether the pending entry actually went away.
            setDone(
                `audit row ${r.cancelled ? "cancelled" : "unchanged"}, ` +
                `queue entry ${r.purged ? "dropped" : "not present"}`,
            );
            // Reload the table the row came from. Without this the row it was
            // cancelled from still reads `queued`, so the operator's next move is
            // to wonder whether the cancel took and press it again -- on a job
            // that is already gone. The store's nonce is the same mechanism the
            // filter bar and the refresh button use, so one reload happens rather
            // than this component fetching its own view of the world.
            if (r.cancelled || r.purged) useAuditFilterStore.getState().refresh();
        } catch (e) {
            setErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    // Sits in the modal header, so it is laid out to fit there: a compact
    // button, and any outcome text truncated with the whole of it on hover
    // rather than allowed to push the close button off the row.
    return (
        <span className="shrink-0 flex items-center gap-2">
            {done && (
                <span className="text-[11px] text-gray-400 max-w-[14rem] truncate" title={done}>
                    {done}
                </span>
            )}
            {err && (
                <span className="text-[11px] text-red-300 max-w-[14rem] truncate" title={err}>
                    {err}
                </span>
            )}
            <button
                type="button"
                className="text-xs bg-red-800 hover:bg-red-700 px-2 py-1 rounded-sm disabled:opacity-50 whitespace-nowrap"
                onClick={() => void onCancel()}
                disabled={busy || done != null}
                title="Cancel this job and drop its queue entry (admin)"
            >
                {busy ? "…" : "Cancel job"}
            </button>
        </span>
    );
};
export default DetailsModal;
