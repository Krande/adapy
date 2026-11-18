// Admin-only badge for running audit sweeps, polled from the audit-active endpoint.

import React from "react";
import { useMeStore } from "@/state/meStore";
import { useViewerPanelStore } from "@/state/viewerPanelStore";
import { useAuditToastStore } from "@/state/auditToastStore";
import { useAuditActivity } from "./useAuditActivity";

function fmtCellElapsed(ms: number | null): string {
    if (ms == null) return "";
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60_000) return `${Math.round(ms / 1000)} s`;
    return `${Math.round(ms / 60_000)} m`;
}

// Ambient indicator for in-progress audit sweeps. Admin-only — the
// endpoint 403s for non-admins. Polls /admin/audit/active every 15s
// so the badge stays current without re-fetching on every render.
// Click → /admin#audit_runs (full audit panel). Hidden whenever
// running_runs == 0 so the slot frees up for the conversion toast
// the moment sweeps drain.
export const AuditActivityBadge: React.FC = () => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const openPanel = useViewerPanelStore((s) => s.openPanel);
    const toastHidden = useAuditToastStore((s) => s.hidden);
    const summary = useAuditActivity(isAdmin);

    if (!isAdmin || toastHidden || !summary || summary.running_runs === 0) return null;
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
            title="Open the audit runs in the panel"
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
