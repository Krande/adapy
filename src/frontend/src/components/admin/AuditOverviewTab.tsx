import React, {useCallback, useEffect, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";
import type {AuditSummary} from "@/services/viewerApi";
import {useAuditFilterStore} from "@/state/auditFilterStore";

// Overview — the Audit tab's landing sub-tab.
//
// It answers the question the operator actually arrives with ("how is the
// sweep going, and what is broken?") before offering them a table. Every
// number is a control: clicking one narrows the shared filter and moves to the
// Log showing exactly the rows behind it.
//
// Counts come from the server (``GET /admin/audit/summary``), not from the
// log's current page. The log is keyset-paginated at 100, so counting what the
// client is holding would under-report any sweep worth summarising.

/** The four states the queue writes, in the order an operator reads them:
 * work not started, work in flight, then the two outcomes. */
const TILES: {status: string; label: string; hint: string; fg: string; border: string; bg: string}[] = [
    {
        status: "queued",
        label: "Queued",
        hint: "waiting on a worker",
        fg: "text-amber-300",
        border: "border-amber-500/40",
        bg: "bg-amber-500/10",
    },
    {
        status: "running",
        label: "Running",
        hint: "in flight now",
        fg: "text-blue-300",
        border: "border-blue-500/40",
        bg: "bg-blue-500/10",
    },
    {
        status: "done",
        label: "Succeeded",
        hint: "completed cleanly",
        fg: "text-emerald-300",
        border: "border-emerald-500/40",
        bg: "bg-emerald-500/10",
    },
    {
        status: "error",
        label: "Failed",
        hint: "needs triage",
        fg: "text-red-300",
        border: "border-red-500/40",
        bg: "bg-red-500/10",
    },
];

const BAR_COLOR: Record<string, string> = {
    done: "bg-emerald-500",
    error: "bg-red-500",
    running: "bg-blue-500",
    queued: "bg-amber-500",
};

const AuditOverviewTab: React.FC<{onDrillDown: () => void}> = ({onDrillDown}) => {
    const filters = useAuditFilterStore((s) => s.filters);
    const toggleStatus = useAuditFilterStore((s) => s.toggleStatus);
    const patch = useAuditFilterStore((s) => s.patch);
    const [summary, setSummary] = useState<AuditSummary | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const reload = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setSummary(await viewerApi.adminAuditSummary(filters));
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, [filters]);

    useEffect(() => {
        void reload();
    }, [reload]);

    // Clicking a tile both narrows and navigates: the operator asked "which
    // ones failed", and a number that only highlights itself has not answered.
    const onTile = (status: string) => {
        const wasSelected = filters.status === status;
        toggleStatus(status);
        if (!wasSelected) onDrillDown();
    };

    const onReason = (message: string) => {
        patch({status: "error"});
        onDrillDown();
        // The reason itself is not a server-side filter — ``key`` matches the
        // filepath, not the error text. Narrowing to failures and letting the
        // operator read the reasons in the log is honest; pretending to filter
        // on something the API cannot filter on would not be.
        void message;
    };

    const counts = summary?.by_status ?? {};
    const settled = (counts.done ?? 0) + (counts.error ?? 0);
    const passRate = settled > 0 ? ((counts.done ?? 0) / settled) * 100 : null;
    const total = summary?.total ?? 0;

    return (
        <div className="h-full overflow-y-auto p-3 sm:p-4 flex flex-col gap-4">
            {error && (
                <div className="text-red-300 text-xs border border-red-900/60 bg-red-950/30 rounded-sm px-3 py-2">
                    {error}
                </div>
            )}

            <div className="grid gap-3" style={{gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))"}}>
                {TILES.map((t) => {
                    const selected = filters.status === t.status;
                    const n = counts[t.status] ?? 0;
                    return (
                        <button
                            key={t.status}
                            onClick={() => onTile(t.status)}
                            aria-pressed={selected}
                            title={
                                selected
                                    ? `Showing only ${t.label.toLowerCase()} — click to clear`
                                    : `Show the ${t.label.toLowerCase()} jobs`
                            }
                            className={
                                "text-left rounded-md px-4 py-3 border transition-colors " +
                                (selected
                                    ? `${t.bg} ${t.border}`
                                    : "bg-gray-800 border-gray-700 hover:border-gray-600")
                            }
                        >
                            <div className="text-xs uppercase tracking-wide text-gray-400">{t.label}</div>
                            <div className={`mt-1 text-3xl font-semibold tabular-nums ${t.fg}`}>
                                {loading && summary === null ? "–" : n.toLocaleString()}
                            </div>
                            <div className="text-[11px] mt-0.5 text-gray-500">{t.hint}</div>
                        </button>
                    );
                })}
            </div>

            <div className="rounded-md bg-gray-800 border border-gray-700 p-4">
                <div className="flex items-baseline justify-between mb-2 gap-3 flex-wrap">
                    <span className="text-xs uppercase tracking-wide text-gray-400">Composition</span>
                    <span className="text-xs text-gray-400">
                        {passRate === null ? (
                            "nothing settled yet"
                        ) : (
                            <>
                                pass rate{" "}
                                <span
                                    className={
                                        "font-semibold tabular-nums " +
                                        (passRate >= 95 ? "text-emerald-300" : "text-amber-300")
                                    }
                                >
                                    {passRate.toFixed(1)}%
                                </span>{" "}
                                <span className="text-gray-500">of {settled.toLocaleString()} settled</span>
                            </>
                        )}
                    </span>
                </div>
                <div className="flex h-2.5 rounded-full overflow-hidden bg-gray-900">
                    {["done", "error", "running", "queued"].map((s) => {
                        const n = counts[s] ?? 0;
                        if (!n || !total) return null;
                        return (
                            <div
                                key={s}
                                className={BAR_COLOR[s]}
                                style={{width: `${(n / total) * 100}%`}}
                                title={`${s}: ${n.toLocaleString()}`}
                            />
                        );
                    })}
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[11px] text-gray-400">
                    {["queued", "running", "done", "error"].map((s) => (
                        <span key={s} className="flex items-center gap-1.5">
                            <span className={`inline-block w-2 h-2 rounded-full ${BAR_COLOR[s]}`}/>
                            {s} <span className="tabular-nums">{(counts[s] ?? 0).toLocaleString()}</span>
                        </span>
                    ))}
                </div>
            </div>

            <div className="grid gap-4" style={{gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))"}}>
                <div className="rounded-md bg-gray-800 border border-gray-700">
                    <div className="px-4 py-2 border-b border-gray-700 text-xs uppercase tracking-wide text-gray-400">
                        Failures by reason
                    </div>
                    <div className="p-2">
                        {(summary?.top_errors ?? []).length === 0 ? (
                            <div className="px-2 py-3 text-xs text-gray-500">
                                {loading ? "Loading…" : "No failures match this filter."}
                            </div>
                        ) : (
                            summary!.top_errors.map((e) => (
                                <button
                                    key={e.error}
                                    onClick={() => onReason(e.error)}
                                    className="w-full text-left px-2 py-1.5 rounded-sm flex items-start gap-3 hover:bg-black/20"
                                    title="Show failed jobs"
                                >
                                    <span className="tabular-nums font-semibold text-red-300 shrink-0 min-w-[1.5rem]">
                                        {e.count}
                                    </span>
                                    <span className="text-xs text-gray-300 break-words">{e.error}</span>
                                </button>
                            ))
                        )}
                    </div>
                </div>

                <div className="rounded-md bg-gray-800 border border-gray-700">
                    <div className="px-4 py-2 border-b border-gray-700 text-xs uppercase tracking-wide text-gray-400">
                        By target
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead className="text-gray-400">
                            <tr>
                                <th className="text-left font-medium px-4 py-1.5">target</th>
                                <th className="text-right font-medium px-2 py-1.5">done</th>
                                <th className="text-right font-medium px-2 py-1.5">failed</th>
                                <th className="text-right font-medium px-4 py-1.5">pending</th>
                            </tr>
                            </thead>
                            <tbody>
                            {(summary?.by_target ?? []).map((row) => {
                                const done = row.counts.done ?? 0;
                                const failed = row.counts.error ?? 0;
                                const pending = (row.counts.queued ?? 0) + (row.counts.running ?? 0);
                                return (
                                    <tr key={row.target} className="border-t border-gray-700">
                                        <td className="px-4 py-1.5 font-mono text-gray-200">{row.target}</td>
                                        <td className="px-2 py-1.5 text-right tabular-nums text-emerald-300">
                                            {done || "—"}
                                        </td>
                                        <td
                                            className={
                                                "px-2 py-1.5 text-right tabular-nums " +
                                                (failed ? "text-red-300" : "text-gray-600")
                                            }
                                        >
                                            {failed || "—"}
                                        </td>
                                        <td className="px-4 py-1.5 text-right tabular-nums text-gray-400">
                                            {pending || "—"}
                                        </td>
                                    </tr>
                                );
                            })}
                            {(summary?.by_target ?? []).length === 0 && (
                                <tr>
                                    <td colSpan={4} className="px-4 py-3 text-gray-500">
                                        {loading ? "Loading…" : "Nothing matches this filter."}
                                    </td>
                                </tr>
                            )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default AuditOverviewTab;
