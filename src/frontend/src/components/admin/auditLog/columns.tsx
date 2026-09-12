import React, {useState} from "react";
import {AuditEntry, viewerApi} from "@/services/viewerApi";
import type {DataTableColumn} from "@/components/common/DataTable";
import {formatTs, hasDetails, isWasmEntry, shortSub, statusClass, userTooltip} from "./format";

// Column definitions for the desktop audit-log table, plus the small badges
// its cells render.

export const WasmBadge: React.FC = () => (
    <span
        className="ml-1 inline-block rounded bg-purple-900/60 text-purple-200 px-1 text-[9px] font-semibold align-middle"
        title="Converted in-browser (WASM)"
    >
        WASM
    </span>
);

// Per-row badge for the issue-bot's sync status on failed user
// conversions (M5b). Only renders on rows the bot is actually
// allowed to touch: failed conversions not attached to an audit
// run (audit-run-attached rows go through the parent run's bot
// pass — showing a badge here would imply double-processing). A
// row that hasn't been claimed yet (status NULL) shows a
// 'pending' pill so the operator knows the bot will get to it.
const ISSUE_BOT_BADGE_LOG: Record<string, {cls: string; label: string}> = {
    done:    {cls: "bg-emerald-900/40 border-emerald-700 text-emerald-200", label: "issue synced"},
    skipped: {cls: "bg-gray-800 border-gray-600 text-gray-400",             label: "bot disabled"},
    failed:  {cls: "bg-red-900/40 border-red-700 text-red-200",             label: "issue sync failed"},
    syncing: {cls: "bg-blue-900/40 border-blue-700 text-blue-200",          label: "syncing…"},
};

export const IssueBotBadge: React.FC<{
    entry: AuditEntry;
    onChanged: () => void;
}> = ({entry, onChanged}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const isFailed = entry.status === "error" || entry.status === "failed";
    if (!isFailed) return null;
    if (entry.audit_run_id) {
        // Audit-run-attached rows: deliberately silent — the parent
        // run's badge tracks the bot for the batch.
        return null;
    }
    const status = entry.issue_bot_status;
    const badge = status
        ? ISSUE_BOT_BADGE_LOG[status] || {
            cls: "bg-gray-800 border-gray-600 text-gray-400",
            label: status,
        }
        : {cls: "bg-amber-900/30 border-amber-700 text-amber-300", label: "issue pending"};
    const retry = async () => {
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditLogSyncIssue(entry.id);
            // Give the background task a moment to flip status before
            // we re-fetch — otherwise the badge briefly snaps back
            // to its old value. A single fixed delay, not a poll.
            await new Promise((r) => setTimeout(r, 600));
            onChanged();
        } catch (e) {
            setErr((e as Error).message || "retry failed");
        } finally {
            setBusy(false);
        }
    };
    return (
        <span className="ml-2 inline-flex items-center gap-1">
            <span
                className={`px-1.5 py-0.5 rounded-sm border text-[10px] ${badge.cls}`}
                title={entry.issue_bot_last_error || badge.label}
            >
                {badge.label}
            </span>
            {(status === "failed" || status === "done") && (
                <button
                    type="button"
                    onClick={retry}
                    disabled={busy}
                    className="text-[10px] text-blue-400 hover:text-blue-300 disabled:opacity-50 no-drag"
                    title="Re-run the issue-bot for this row"
                >
                    {busy ? "queued…" : "resync"}
                </button>
            )}
            {err && (
                <span className="text-[10px] text-red-400" role="alert">{err}</span>
            )}
        </span>
    );
};

// Header/cell classes the table used with its own <Th>/<Td> helpers.
export const AUDIT_LOG_TH_CLASS = "px-3 py-2 font-medium text-gray-300 whitespace-nowrap";
// Truncation lives at the cell level so long values (paths, error
// messages, full subs) don't break layout — but we let the column
// widths do the gating now via <colgroup>, not a hard 20ch cap.
export const AUDIT_LOG_TD_CLASS = "px-3 py-1 truncate";

export interface AuditLogColumnHandlers {
    onDetails: (entry: AuditEntry) => void;
    onChanged: () => void;
}

// Time/User/Scope/Action/Target/Status are predictable in width, so we fix
// them and let Key flex to consume the rest of the row. ``min-w`` on each col
// pins a floor so a stale w-16 doesn't truncate a 5-digit ID; the Key column
// stays flexible (``min-w-[14rem]``) so long source paths get the leftover
// width.
export function buildAuditLogColumns(h: AuditLogColumnHandlers): DataTableColumn<AuditEntry>[] {
    return [
        {
            key: "id",
            header: "ID",
            col: {className: "min-w-[5rem]"},
            cell: (e) => <span className="font-mono text-gray-300">#{e.id}</span>,
        },
        {
            key: "time",
            header: "Time",
            col: {className: "min-w-[11rem]"},
            title: (e) => e.ts || "",
            cell: (e) => formatTs(e.ts),
        },
        {
            key: "user",
            header: "User",
            col: {className: "min-w-[8rem]"},
            title: (e) => userTooltip(e),
            cell: (e) => e.user_display_name || shortSub(e.user_sub),
        },
        {
            key: "scope",
            header: "Scope",
            col: {className: "min-w-[12rem]"},
            title: (e) => e.scope_id || "",
            cell: (e) => (
                <>
                    {e.scope_kind}
                    {e.scope_id ? `:${shortSub(e.scope_id)}` : ""}
                </>
            ),
        },
        {
            key: "action",
            header: "Action",
            col: {className: "min-w-[6rem]"},
            cell: (e) => e.action,
        },
        {
            key: "device",
            header: "Device",
            col: {className: "min-w-[14rem]"},
            title: (e) => e.device_id || "",
            cell: (e) => e.device_id ? (
                <span className="font-mono text-gray-400">{e.device_id.slice(0, 8)}</span>
            ) : (
                <span className="text-gray-600">—</span>
            ),
        },
        {
            key: "key",
            header: "Key",
            col: {className: "min-w-[6rem]"},
            title: (e) => e.key || "",
            cell: (e) => e.key || "",
        },
        {
            key: "target",
            header: "Target",
            col: {className: "min-w-[6rem]"},
            cell: (e) => (
                <>
                    {e.target_format || ""}
                    {isWasmEntry(e) && <WasmBadge/>}
                </>
            ),
        },
        {
            key: "status",
            header: "Status",
            title: (e) => e.error || "",
            cell: (e) => (
                <>
                    <span className={statusClass(e.status)}>{e.status || ""}</span>
                    {hasDetails(e) && (
                        <button
                            type="button"
                            className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-500 text-gray-300 hover:text-white hover:border-white text-[10px] font-bold leading-none align-middle no-drag"
                            onClick={() => h.onDetails(e)}
                            title={e.error ? "Show error / metrics" : "Show metrics"}
                            aria-label="Show details"
                        >
                            i
                        </button>
                    )}
                    <IssueBotBadge entry={e} onChanged={h.onChanged}/>
                </>
            ),
        },
    ];
}
