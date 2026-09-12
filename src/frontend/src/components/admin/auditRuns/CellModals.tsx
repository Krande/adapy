import React, {useEffect, useState} from "react";
import {viewerApi, AuditRun, AuditRunJob, AuditCellHistoryRow} from "@/services/viewerApi";
import {DataTable, DataTableColumn} from "@/components/common/DataTable";
import {formatBytes, formatMillis} from "@/utils/format";

const HISTORY_TH = "px-2 py-1 border-b border-gray-700";
const HISTORY_TD = "px-2 py-1 border-b border-gray-800";

const HISTORY_COLUMNS: DataTableColumn<AuditCellHistoryRow>[] = [
    {
        key: "when",
        header: "when",
        headerClassName: "text-left " + HISTORY_TH,
        cellClassName: HISTORY_TD + " text-gray-300 whitespace-nowrap",
        cell: (h) => h.ts ? new Date(h.ts).toLocaleString() : "—",
    },
    {
        key: "status",
        header: "status",
        headerClassName: "text-left " + HISTORY_TH,
        cellClassName: HISTORY_TD + " text-gray-200",
        cell: (h) => h.status,
    },
    {
        key: "dur",
        header: "dur",
        headerClassName: "text-right " + HISTORY_TH,
        cellClassName: HISTORY_TD + " text-right text-gray-300",
        cell: (h) => h.duration_ms != null ? `${(h.duration_ms / 1000).toFixed(1)}s` : "—",
    },
    {
        key: "peak_rss",
        header: "peak RSS",
        headerClassName: "text-right " + HISTORY_TH,
        cellClassName: HISTORY_TD + " text-right text-gray-300",
        cell: (h) => h.peak_rss_kb != null ? `${Math.round(h.peak_rss_kb / 1024)}MB` : "—",
    },
    {
        key: "worker",
        header: "worker",
        headerClassName: "text-left " + HISTORY_TH,
        cellClassName: HISTORY_TD + " text-gray-400 font-mono truncate max-w-[120px]",
        title: (h) => h.worker_image_tag || "",
        cell: (h) => h.worker_image_tag || "—",
    },
    {
        key: "error",
        header: "error",
        headerClassName: "text-left " + HISTORY_TH,
        cellClassName: HISTORY_TD + " text-red-300 truncate max-w-[220px]",
        title: (h) => h.error || "",
        cell: (h) => h.error || "",
    },
];

// Cross-run history for one grid cell (source × target), opened from the
// cell context menu. Newest result first, so a run-to-run regression in
// duration / peak RSS / status is visible at a glance.
const CellHistoryModal: React.FC<{
    cell: {key: string; target: string};
    onClose: () => void;
}> = ({cell, onClose}) => {
    const [rows, setRows] = useState<AuditCellHistoryRow[] | null>(null);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        setRows(null);
        setErr(null);
        (async () => {
            try {
                const r = await viewerApi.adminAuditCellHistory(cell.key, cell.target);
                if (!cancelled) setRows(r.history);
            } catch (e) {
                if (!cancelled) setErr((e as Error).message || "history load failed");
            }
        })();
        return () => { cancelled = true; };
    }, [cell.key, cell.target]);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
            <div
                className="bg-gray-900 border border-gray-700 rounded-sm max-w-3xl w-full max-h-[80vh] flex flex-col"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800">
                    <div className="text-sm text-gray-200 font-mono truncate" title={`${cell.key} .${cell.target}`}>
                        {cell.key} · .{cell.target}
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="text-gray-400 hover:text-gray-200 text-lg leading-none px-2"
                        aria-label="Close"
                    >
                        ×
                    </button>
                </div>
                <div className="overflow-auto p-2">
                    {err && <div className="text-xs text-red-400 px-2 py-2" role="alert">{err}</div>}
                    {!rows && !err && <div className="text-xs text-gray-400 px-2 py-4">Loading…</div>}
                    {rows && rows.length === 0 && (
                        <div className="text-xs text-gray-400 px-2 py-4">No historic results for this cell.</div>
                    )}
                    {rows && rows.length > 0 && (
                        <DataTable
                            wrap={false}
                            columns={HISTORY_COLUMNS}
                            rows={rows}
                            rowKey={(h) => h.id}
                            className="text-xs border-collapse w-full"
                            theadClassName="text-gray-400"
                            rowClassName="hover:bg-gray-800/40"
                        />
                    )}
                </div>
            </div>
        </div>
    );
};

// Full info for one grid cell — status, metrics and the error — plus the run
// it belongs to. Opened from the cell context menu so the detail is reachable
// on touch (no hover tooltip) and gives the whole error on desktop too.
const CellDetailsModal: React.FC<{
    run: AuditRun;
    file: string;
    target: string;
    job: AuditRunJob | undefined;
    onClose: () => void;
}> = ({run, file, target, job, onClose}) => {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    const rows: Array<[string, string]> = [];
    rows.push(["Run", run.seq != null ? `#${run.seq}` : run.id]);
    rows.push(["Scope", run.scope]);
    if (run.trigger) rows.push(["Trigger", run.trigger]);
    rows.push(["Source", file]);
    rows.push(["Target", `.${target}`]);
    if (job) {
        if (job.status) rows.push(["Status", job.status]);
        if (job.ts) rows.push(["When", new Date(job.ts).toLocaleString()]);
        if (job.duration_ms != null) rows.push(["Elapsed", formatMillis(job.duration_ms)]);
        if (job.peak_rss_kb != null) rows.push(["Peak RSS", formatBytes(job.peak_rss_kb * 1024)]);
        if (job.cpu_user_ms != null) rows.push(["CPU user", formatMillis(job.cpu_user_ms)]);
        if (job.cpu_sys_ms != null) rows.push(["CPU sys", formatMillis(job.cpu_sys_ms)]);
        if (job.read_bytes != null) rows.push(["Read", formatBytes(job.read_bytes)]);
        if (job.write_bytes != null) rows.push(["Write", formatBytes(job.write_bytes)]);
        if (job.worker_image_tag) rows.push(["Worker", job.worker_image_tag]);
        if (job.job_id) rows.push(["Job id", job.job_id]);
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
            <div
                className="bg-gray-900 border border-gray-700 rounded-sm max-w-2xl w-full max-h-[80vh] flex flex-col"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800">
                    <div className="text-sm text-gray-200 font-mono truncate" title={`${file} .${target}`}>
                        {file} · .{target}
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="text-gray-400 hover:text-gray-200 text-lg leading-none px-2"
                        aria-label="Close"
                    >
                        ×
                    </button>
                </div>
                <div className="overflow-auto p-3 space-y-3">
                    {!job && (
                        <div className="text-xs text-gray-400">No result recorded for this cell yet.</div>
                    )}
                    <table className="text-xs">
                        <tbody>
                            {rows.map(([k, v]) => (
                                <tr key={k}>
                                    <td className="text-gray-400 pr-3 py-0.5 align-top whitespace-nowrap">{k}</td>
                                    <td className="text-gray-200 font-mono break-all py-0.5">{v}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {job?.error && (
                        <div>
                            <div className="text-xs text-gray-400 mb-1">Error</div>
                            <pre className="text-xs text-red-300 whitespace-pre-wrap bg-gray-950 border border-gray-800 rounded-sm p-2 overflow-auto max-h-60">
                                {job.error}
                            </pre>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export {CellHistoryModal, CellDetailsModal};
