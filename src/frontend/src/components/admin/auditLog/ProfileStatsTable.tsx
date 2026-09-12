import React, {useEffect, useMemo, useState} from "react";
import {ApiError, ProfileStatsRow, viewerApi} from "@/services/viewerApi";
import {DataTable, DataTableColumn, DataTableSort} from "@/components/common/DataTable";
import {formatPercall, shortFile} from "./format";

// Inline per-function stats — sortable and searchable so the operator can find
// hot frames without leaving the UI. Server-side pstats parse keeps the SPA
// bundle free of marshal/pickle parsers.

const STATS_TH_CLASS = "px-2 py-1 select-none cursor-pointer hover:text-white whitespace-nowrap ";

const STATS_COLUMNS: DataTableColumn<ProfileStatsRow>[] = [
    {
        key: "func",
        header: "function",
        headerClassName: STATS_TH_CLASS + "text-left",
        cellClassName: "px-2 py-0.5 text-left text-gray-200 truncate max-w-[20rem]",
        title: (r) => `${r.file}:${r.line}`,
        sortValue: (r) => r.func,
        cell: (r) => (
            <>
                <span className="text-gray-500">{shortFile(r.file)}:{r.line} </span>
                {r.func}
            </>
        ),
    },
    {
        key: "ncalls",
        header: "ncalls",
        headerClassName: STATS_TH_CLASS + "text-right",
        cellClassName: "px-2 py-0.5 text-gray-300",
        sortValue: (r) => r.ncalls,
        sortDefaultDesc: true,
        cell: (r) => r.ncalls.toLocaleString(),
    },
    {
        key: "primitive_calls",
        header: "prim",
        headerClassName: STATS_TH_CLASS + "text-right",
        cellClassName: "px-2 py-0.5 text-gray-400",
        sortValue: (r) => r.primitive_calls,
        sortDefaultDesc: true,
        cell: (r) => r.primitive_calls.toLocaleString(),
    },
    {
        key: "tottime",
        header: "tottime",
        headerClassName: STATS_TH_CLASS + "text-right",
        cellClassName: "px-2 py-0.5 text-gray-200",
        sortValue: (r) => r.tottime,
        sortDefaultDesc: true,
        cell: (r) => r.tottime.toFixed(3),
    },
    {
        key: "percall_tot",
        header: "percall",
        headerClassName: STATS_TH_CLASS + "text-right",
        cellClassName: "px-2 py-0.5 text-gray-400",
        sortValue: (r) => r.percall_tot,
        sortDefaultDesc: true,
        cell: (r) => formatPercall(r.percall_tot),
    },
    {
        key: "cumtime",
        header: "cumtime",
        headerClassName: STATS_TH_CLASS + "text-right",
        cellClassName: "px-2 py-0.5 text-gray-200",
        sortValue: (r) => r.cumtime,
        sortDefaultDesc: true,
        cell: (r) => r.cumtime.toFixed(3),
    },
    {
        key: "percall_cum",
        header: "percum",
        headerClassName: STATS_TH_CLASS + "text-right",
        cellClassName: "px-2 py-0.5 text-gray-400",
        sortValue: (r) => r.percall_cum,
        sortDefaultDesc: true,
        cell: (r) => formatPercall(r.percall_cum),
    },
];

const ProfileStatsTable: React.FC<{auditId: number; totalWallMs: number | null}> = ({auditId, totalWallMs}) => {
    const [resp, setResp] = useState<{rows: ProfileStatsRow[]; total_tottime: number} | null>(null);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [filter, setFilter] = useState("");
    const [sort, setSort] = useState<DataTableSort>({key: "cumtime", desc: true});

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setErr(null);
        viewerApi.adminProfileStats(auditId, 1000)
            .then((r) => {
                if (cancelled) return;
                setResp({rows: r.rows, total_tottime: r.total_tottime});
            })
            .catch((e) => {
                if (cancelled) return;
                setErr(e instanceof ApiError ? e.detail || e.message : String(e));
            })
            .finally(() => {
                if (cancelled) return;
                setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [auditId]);

    const filtered = useMemo(() => {
        if (!resp) return [];
        const q = filter.trim().toLowerCase();
        return q
            ? resp.rows.filter(
                  (r) =>
                      r.func.toLowerCase().includes(q) ||
                      r.file.toLowerCase().includes(q),
              )
            : resp.rows;
    }, [resp, filter]);

    if (loading) {
        return (
            <div className="text-[10px] text-gray-400">Loading profile stats…</div>
        );
    }
    if (err) {
        return (
            <div className="text-[10px] text-red-300 break-all">profile stats: {err}</div>
        );
    }
    if (!resp || resp.rows.length === 0) {
        return <div className="text-[10px] text-gray-400">No frames recorded.</div>;
    }

    // Top-N visual: a single horizontal bar per function (cumtime as % of
    // total tottime) gives a "where did the time go" cue alongside the
    // table — same Pareto graph snakeviz draws, just inline.
    const topByCum = [...resp.rows].sort((a, b) => b.cumtime - a.cumtime).slice(0, 10);
    const maxCum = topByCum.length > 0 ? topByCum[0].cumtime : 1;

    return (
        <div className="text-[11px] space-y-2">
            <div className="text-gray-400">
                <span className="font-mono">{resp.rows.length}</span> functions ·{" "}
                <span className="font-mono">{resp.total_tottime.toFixed(2)}s</span> total self-time
                {totalWallMs != null ? (
                    <span> · wall <span className="font-mono">{(totalWallMs / 1000).toFixed(2)}s</span></span>
                ) : null}
            </div>
            {/* Top-10 cumulative-time bar chart */}
            <div className="space-y-0.5 bg-gray-900/60 border border-gray-800 rounded-sm p-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                    Top 10 by cumulative time
                </div>
                {topByCum.map((r, i) => (
                    <div key={`${r.file}:${r.line}:${r.func}:${i}`} className="flex items-center gap-2">
                        <div className="flex-1 min-w-0">
                            <div className="font-mono text-gray-200 truncate" title={`${r.file}:${r.line}`}>
                                {r.func}
                            </div>
                            <div className="h-1 bg-gray-800 rounded-sm overflow-hidden">
                                <div
                                    className="h-full bg-blue-500"
                                    style={{width: `${maxCum > 0 ? (r.cumtime / maxCum) * 100 : 0}%`}}
                                />
                            </div>
                        </div>
                        <div className="font-mono text-gray-300 w-16 text-right shrink-0">
                            {r.cumtime.toFixed(3)}s
                        </div>
                    </div>
                ))}
            </div>
            <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by function or file…"
                className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 w-full text-white"
            />
            <div className="overflow-auto max-h-96 border border-gray-800 rounded-sm">
                <DataTable
                    wrap={false}
                    columns={STATS_COLUMNS}
                    rows={filtered}
                    rowKey={(r, i) => `${r.file}:${r.line}:${r.func}:${i}`}
                    className="w-full font-mono"
                    stickyHeader
                    theadClassName="bg-gray-800 text-gray-300"
                    headerRowClassName="text-right"
                    rowClassName="border-t border-gray-800 text-right"
                    sort={sort}
                    onSortChange={setSort}
                    emptyState={
                        <div className="text-center text-gray-500 py-3">No frames match the filter.</div>
                    }
                />
            </div>
        </div>
    );
};

export default ProfileStatsTable;
