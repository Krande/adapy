import React, {useState} from "react";
import {viewerApi, AuditRun} from "@/services/viewerApi";

// Per-run action buttons shown in the selected run's header, and the
// issue-bot status badge.

const CancelRunButton: React.FC<{
    run: AuditRun;
    onCancelled: () => void;
}> = ({run, onCancelled}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const onClick = async () => {
        if (!window.confirm(
            `Abort audit run "${run.scope}"? Queued cells will be marked cancelled.`,
        )) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunCancel(run.id);
            onCancelled();
        } catch (e) {
            setErr((e as Error).message || "cancel failed");
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="flex items-center gap-2">
            <button
                type="button"
                onClick={onClick}
                disabled={busy}
                className="text-xs px-2 py-1 border border-red-700 text-red-300 hover:bg-red-900/30 rounded-sm disabled:opacity-50"
                title="Abort this run; pending cells get marked cancelled."
            >
                {busy ? "Aborting…" : "Cancel run"}
            </button>
            {err && <span className="text-[11px] text-red-400" role="alert">{err}</span>}
        </div>
    );
};

const ReDispatchButton: React.FC<{
    run: AuditRun;
    onDispatched: () => void;
}> = ({run, onDispatched}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const onClick = async () => {
        if (!window.confirm(
            `Re-run this audit against "${run.scope}"? A new run is created with the same scope, pool and settings.`,
        )) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunReDispatch(run.id);
            onDispatched();
        } catch (e) {
            setErr((e as Error).message || "re-run failed");
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="flex items-center gap-2">
            <button
                type="button"
                onClick={onClick}
                disabled={busy}
                className="text-xs px-2 py-1 border border-blue-700 text-blue-300 hover:bg-blue-900/30 rounded-sm disabled:opacity-50"
                title="Create a new audit run with this run's scope / pool / settings."
            >
                {busy ? "Starting…" : "Re-run audit"}
            </button>
            {err && <span className="text-[11px] text-red-400" role="alert">{err}</span>}
        </div>
    );
};

// Kick off a cross-format parity validation pass on a finished run. The cells
// are appended to *this* run (it reopens to 'running' until they land), not a
// new run. Dispatched at most once per run — the button disables once a
// validation has already run (via the toggle or a prior click).
const ValidateRunButton: React.FC<{
    run: AuditRun;
    onValidated: () => void;
}> = ({run, onValidated}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const alreadyValidated = !!run.auto_validate_dispatched_at;
    const onClick = async () => {
        if (!window.confirm(
            `Run validation on "${run.scope}"? Cross-format parity cells are appended to this run.`,
        )) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunValidate(run.id);
            onValidated();
        } catch (e) {
            setErr((e as Error).message || "validation failed");
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="flex items-center gap-2">
            <button
                type="button"
                onClick={onClick}
                disabled={busy || alreadyValidated}
                className="text-xs px-2 py-1 border border-teal-700 text-teal-300 hover:bg-teal-900/30 rounded-sm disabled:opacity-50 disabled:cursor-not-allowed"
                title={
                    alreadyValidated
                        ? "Validation already dispatched for this run."
                        : "Append a cross-format parity validation pass to this run."
                }
            >
                {busy ? "Starting…" : alreadyValidated ? "Validated" : "Validate"}
            </button>
            {err && <span className="text-[11px] text-red-400" role="alert">{err}</span>}
        </div>
    );
};

// Delete a finished/aborted run and its audit_log rows (parity cascades).
const DeleteRunButton: React.FC<{
    run: AuditRun;
    onDeleted: () => void;
}> = ({run, onDeleted}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const label = run.seq != null ? `#${run.seq}` : run.scope;
    const onClick = async () => {
        if (!window.confirm(
            `Delete audit run ${label} ("${run.scope}")? Its results are removed permanently.`,
        )) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunDelete(run.id);
            onDeleted();
        } catch (e) {
            setErr((e as Error).message || "delete failed");
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="flex items-center gap-2">
            <button
                type="button"
                onClick={onClick}
                disabled={busy}
                className="text-xs px-2 py-1 border border-red-800 text-red-300 hover:bg-red-900/30 rounded-sm disabled:opacity-50"
                title="Delete this run and its results."
            >
                {busy ? "Deleting…" : "Delete"}
            </button>
            {err && <span className="text-[11px] text-red-400" role="alert">{err}</span>}
        </div>
    );
};

const ISSUE_BOT_BADGE: Record<string, {cls: string; label: string}> = {
    done:     {cls: "bg-emerald-900/40 border-emerald-700 text-emerald-200", label: "issues synced"},
    skipped:  {cls: "bg-gray-800 border-gray-600 text-gray-400",             label: "issues skipped"},
    failed:   {cls: "bg-red-900/40 border-red-700 text-red-200",             label: "issue sync failed"},
    syncing:  {cls: "bg-blue-900/40 border-blue-700 text-blue-200",          label: "issues syncing…"},
};

// Surface the per-run issue-bot outcome inline with the rest of the
// run header. Manual retry button is shown only when the bot
// terminated in 'failed' so a happy-path run doesn't get extra
// clickable noise.
const IssueBotStatus: React.FC<{
    run: AuditRun;
    onChanged: () => void;
}> = ({run, onChanged}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    if (run.status !== "finished" || !run.issue_bot_status) {
        return null;
    }
    const badge = ISSUE_BOT_BADGE[run.issue_bot_status] || {
        cls: "bg-gray-800 border-gray-600 text-gray-400",
        label: run.issue_bot_status,
    };
    const retry = async () => {
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunSyncIssues(run.id);
            onChanged();
        } catch (e) {
            setErr((e as Error).message || "retry failed");
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="mt-1 flex items-center gap-2 text-[11px]">
            <span
                className={`px-1.5 py-0.5 rounded-sm border ${badge.cls}`}
                title={run.issue_bot_last_error || badge.label}
            >
                {badge.label}
            </span>
            {(run.issue_bot_status === "failed" || run.issue_bot_status === "done") && (
                <button
                    type="button"
                    onClick={retry}
                    disabled={busy}
                    className="text-blue-400 hover:text-blue-300 disabled:opacity-50"
                    title="Re-run the issue-bot sync for this run"
                >
                    {busy ? "queued…" : "resync"}
                </button>
            )}
            {err && <span className="text-red-400" role="alert">{err}</span>}
        </div>
    );
};

export {CancelRunButton, ReDispatchButton, ValidateRunButton, DeleteRunButton, IssueBotStatus};
