import React, {useCallback, useEffect, useMemo, useState} from "react";
import {
    PerfCell,
    PerfHotspotRow,
    PerfHotspotsResp,
    PerfReport,
    PerfThresholdsResp,
    viewerApi,
} from "@/services/viewerApi";

// Admin tab — cross-conversion performance dashboard (M6 of
// plan/v2/notes_admin_audit_panel.md).
//
// One row per (source_ext, target_format) cell aggregated over the
// last N days of convert jobs (audit + prod combined by default).
// Streaming-candidate badge fires from a rule-based classifier on
// the backend; the per-row tooltip lists which signals tripped.

type SortKey =
    | "cell"
    | "samples"
    | "failures"
    | "p50_duration"
    | "p95_duration"
    | "p95_rss"
    | "rss_per_mb"
    | "streaming";

const TIME_WINDOWS: {label: string; days: number}[] = [
    {label: "Last 24 h", days: 1},
    {label: "Last 7 days", days: 7},
    {label: "Last 30 days", days: 30},
    {label: "Last 90 days", days: 90},
];

function fmtBytes(n: number | null): string {
    if (n == null) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtKB(n: number | null): string {
    return n == null ? "—" : fmtBytes(n * 1024);
}

function fmtMs(n: number | null): string {
    if (n == null) return "—";
    if (n < 1000) return `${n} ms`;
    return `${(n / 1000).toFixed(1)} s`;
}

function fmtPct(n: number | null | undefined): string {
    if (n == null) return "—";
    return `${(n * 100).toFixed(1)}%`;
}

function fmtRatio(n: number | null): string {
    if (n == null) return "—";
    return `${n.toFixed(1)}×`;
}

const ThresholdEditor: React.FC<{
    initial: PerfThresholdsResp;
    onSaved: (resp: PerfThresholdsResp) => void;
}> = ({initial, onSaved}) => {
    const [draft, setDraft] = useState<Record<string, string>>(() => {
        const out: Record<string, string> = {};
        for (const k of Object.keys(initial.defaults)) {
            out[k] = String(initial.thresholds[k] ?? initial.defaults[k]);
        }
        return out;
    });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const save = useCallback(async () => {
        setBusy(true);
        setErr(null);
        const payload: Record<string, number | null> = {};
        for (const [k, v] of Object.entries(draft)) {
            if (!v.trim()) {
                payload[k] = null;
                continue;
            }
            const n = Number(v);
            if (Number.isNaN(n)) {
                setErr(`${k}: not a number`);
                setBusy(false);
                return;
            }
            payload[k] = n;
        }
        try {
            const r = await viewerApi.adminPerfThresholdsSet(payload);
            onSaved(r);
        } catch (e) {
            setErr((e as Error).message || "save failed");
        } finally {
            setBusy(false);
        }
    }, [draft, onSaved]);

    return (
        <div className="border border-gray-800 rounded-sm bg-gray-900/40 p-3 text-xs text-gray-300 space-y-2">
            <div className="font-medium text-gray-100 text-sm">
                Streaming-candidate thresholds
            </div>
            <div className="text-gray-500">
                A cell flags as a streaming candidate when ANY of these
                thresholds is exceeded by the corresponding metric.
                Empty field falls back to the shipped default.
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {Object.entries(initial.defaults).map(([k, dflt]) => (
                    <label key={k} className="flex flex-col gap-1">
                        <span className="text-gray-400 font-mono text-[11px]">
                            {k} <span className="text-gray-600">(default {dflt})</span>
                        </span>
                        <input
                            type="text"
                            value={draft[k] ?? ""}
                            onChange={(e) => setDraft({...draft, [k]: e.target.value})}
                            placeholder={String(dflt)}
                            className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono"
                        />
                    </label>
                ))}
            </div>
            <div className="flex items-center gap-3 pt-1">
                <button
                    type="button"
                    onClick={save}
                    disabled={busy}
                    className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-xs px-3 py-1 rounded-sm"
                >
                    {busy ? "Saving…" : "Save thresholds"}
                </button>
                {err && <span className="text-red-400">{err}</span>}
            </div>
        </div>
    );
};

// One pill per fired signal. The IO-bound flag (``cpu_fraction_max``)
// renders distinctly from the streaming-candidate flags so the
// operator can see at a glance whether a slow cell is CPU-bound (RSS
// or duration signals) vs IO-bound (cpu fraction signal). All other
// fired signals collapse into a single "consider streaming" pill.
const FlagsBadges: React.FC<{
    cell: PerfCell;
    reasons: Record<string, string>;
}> = ({cell, reasons}) => {
    const signals = cell.streaming.signals;
    if (signals.length === 0) {
        return <span className="text-gray-600">—</span>;
    }
    const ioBound = signals.includes("cpu_fraction_max");
    const otherSignals = signals.filter((s) => s !== "cpu_fraction_max");
    return (
        <div className="flex gap-1 flex-wrap items-center">
            {otherSignals.length > 0 && (
                <span
                    className="inline-block px-1.5 py-0.5 bg-amber-900/50 border border-amber-600 text-amber-200 rounded-sm text-[10px]"
                    title={otherSignals.map((s) => `• ${reasons[s] || s}`).join("\n")}
                >
                    streaming
                </span>
            )}
            {ioBound && (
                <span
                    className="inline-block px-1.5 py-0.5 bg-sky-900/50 border border-sky-600 text-sky-200 rounded-sm text-[10px]"
                    title={reasons["cpu_fraction_max"] || "IO-bound"}
                >
                    IO-bound
                </span>
            )}
        </div>
    );
};

// Hotspots panel: lazy-fetch top functions for one cell. Empty
// state distinguishes "profiling disabled" (no profiles_in_window)
// from "no aggregated data yet" (the parser hasn't caught up).
const HotspotsPanel: React.FC<{
    sourceExt: string;
    targetFormat: string;
    sinceDays: number;
}> = ({sourceExt, targetFormat, sinceDays}) => {
    const [data, setData] = useState<PerfHotspotsResp | null>(null);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setErr(null);
        (async () => {
            try {
                const r = await viewerApi.adminPerfHotspots({
                    source_ext: sourceExt,
                    target_format: targetFormat,
                    since: sinceDays,
                    limit: 25,
                });
                if (!cancelled) setData(r);
            } catch (e) {
                if (!cancelled) setErr((e as Error).message || "fetch failed");
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [sourceExt, targetFormat, sinceDays]);

    if (loading) {
        return <div className="text-xs text-gray-500 italic">Loading hotspots…</div>;
    }
    if (err) {
        return <div className="text-xs text-red-400">Hotspots failed: {err}</div>;
    }
    if (!data || data.profiles_in_window === 0) {
        return (
            <div className="text-xs text-gray-500 italic">
                No profile data in the window. Toggle{" "}
                <code className="text-gray-400">profile_conversions</code>{" "}
                on (admin settings or per-job conversion options), run a few
                conversions, then check back — the background parser indexes new
                profiles every ~30 s.
            </div>
        );
    }
    return (
        <div className="space-y-2">
            <div className="text-[11px] text-gray-500">
                Top {data.functions.length} functions across {data.profiles_in_window} profiled run
                {data.profiles_in_window === 1 ? "" : "s"} in the last {data.since_days} day
                {data.since_days === 1 ? "" : "s"}.
                Ranked by cumulative time (sum across all matching profiles).
            </div>
            <div className="overflow-x-auto">
                <table className="text-[11px] border-collapse w-full">
                    <thead className="text-gray-400">
                        <tr>
                            <Th align="right">cumtime (s)</Th>
                            <Th align="right">ncalls</Th>
                            <Th align="right">in N runs</Th>
                            <Th>function</Th>
                            <Th>file:line</Th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.functions.map((row) => (
                            <tr
                                key={`${row.file}:${row.line}:${row.func}`}
                                className="border-t border-gray-800 hover:bg-gray-900"
                            >
                                <td className="px-2 py-1 text-right text-amber-200 font-mono">
                                    {row.agg_cumtime.toFixed(2)}
                                </td>
                                <td className="px-2 py-1 text-right text-gray-400 font-mono">
                                    {row.agg_ncalls.toLocaleString()}
                                </td>
                                <td className="px-2 py-1 text-right text-gray-500">
                                    {row.profiles_seen}
                                </td>
                                <td className="px-2 py-1 font-mono text-gray-200">
                                    {row.func}
                                </td>
                                <td className="px-2 py-1 font-mono text-gray-500 truncate max-w-xs" title={`${row.file}:${row.line}`}>
                                    {row.file ? `${row.file}:${row.line}` : "—"}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

const PerformanceTab: React.FC = () => {
    const [report, setReport] = useState<PerfReport | null>(null);
    const [thresholds, setThresholds] = useState<PerfThresholdsResp | null>(null);
    const [windowDays, setWindowDays] = useState(30);
    const [trigger, setTrigger] = useState<"all" | "audit" | "user">("all");
    const [sortKey, setSortKey] = useState<SortKey>("p95_rss");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [showThresholds, setShowThresholds] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    // Hotspots drill-down: track which cell is expanded (one at a
    // time keeps the table readable) and lazy-load the hotspots.
    const [expanded, setExpanded] = useState<string | null>(null);

    const loadReport = useCallback(async (days: number, trig: typeof trigger) => {
        setLoading(true);
        setErr(null);
        try {
            const r = await viewerApi.adminPerfReport({since: days, trigger: trig});
            setReport(r);
        } catch (e) {
            setErr((e as Error).message || "load failed");
        } finally {
            setLoading(false);
        }
    }, []);

    const loadThresholds = useCallback(async () => {
        try {
            setThresholds(await viewerApi.adminPerfThresholdsGet());
        } catch {
            // Non-fatal — the table still renders with the response's
            // inline thresholds field.
        }
    }, []);

    useEffect(() => {
        void loadReport(windowDays, trigger);
    }, [windowDays, trigger, loadReport]);

    useEffect(() => { void loadThresholds(); }, [loadThresholds]);

    const sortedCells = useMemo(() => {
        if (!report) return [];
        const dir = sortDir === "asc" ? 1 : -1;
        const get = (c: PerfCell): number | string => {
            switch (sortKey) {
                case "cell": return `${c.source_ext}::${c.target_format}`;
                case "samples": return c.sample_count;
                case "failures": return c.failure_rate;
                case "p50_duration": return c.duration_ms_p50 ?? -1;
                case "p95_duration": return c.duration_ms_p95 ?? -1;
                case "p95_rss": return c.peak_rss_kb_p95 ?? -1;
                case "rss_per_mb": return c.peak_rss_per_source_mb_p95 ?? -1;
                case "streaming": return c.streaming.is_candidate ? 1 : 0;
            }
        };
        return [...report.cells].sort((a, b) => {
            const va = get(a);
            const vb = get(b);
            if (typeof va === "string" && typeof vb === "string") {
                return va.localeCompare(vb) * dir;
            }
            return ((va as number) - (vb as number)) * dir;
        });
    }, [report, sortKey, sortDir]);

    const onHeaderClick = (k: SortKey) => {
        if (k === sortKey) {
            setSortDir(sortDir === "asc" ? "desc" : "asc");
        } else {
            setSortKey(k);
            setSortDir(k === "cell" ? "asc" : "desc");
        }
    };

    const sortArrow = (k: SortKey) =>
        k === sortKey ? (sortDir === "asc" ? " ▲" : " ▼") : "";

    return (
        <div className="flex flex-col h-full overflow-auto">
            <div className="px-3 py-2 border-b border-gray-800 bg-gray-900/40 flex flex-wrap items-center gap-3">
                <label className="text-xs text-gray-300 flex items-center gap-2">
                    <span>Window</span>
                    <select
                        value={windowDays}
                        onChange={(e) => setWindowDays(Number(e.target.value))}
                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                    >
                        {TIME_WINDOWS.map((w) => (
                            <option key={w.days} value={w.days}>{w.label}</option>
                        ))}
                    </select>
                </label>
                <label className="text-xs text-gray-300 flex items-center gap-2">
                    <span>Source</span>
                    <select
                        value={trigger}
                        onChange={(e) => setTrigger(e.target.value as typeof trigger)}
                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                    >
                        <option value="all">all jobs</option>
                        <option value="user">user-driven only</option>
                        <option value="audit">audit-sweep only</option>
                    </select>
                </label>
                <button
                    type="button"
                    onClick={() => setShowThresholds(!showThresholds)}
                    className="text-xs text-blue-400 hover:text-blue-300 ml-auto"
                >
                    {showThresholds ? "hide" : "edit"} thresholds
                </button>
                {report && (
                    <span className="text-[11px] text-gray-500">
                        {report.cells.length} cells · generated {new Date(report.generated_at).toLocaleTimeString()}
                    </span>
                )}
            </div>

            {showThresholds && thresholds && (
                <div className="px-3 py-3 border-b border-gray-800">
                    <ThresholdEditor
                        initial={thresholds}
                        onSaved={(r) => {
                            setThresholds(r);
                            // Re-fetch the report so the new thresholds
                            // re-run the classifier on the current data.
                            void loadReport(windowDays, trigger);
                        }}
                    />
                </div>
            )}

            {err && (
                <div className="text-xs text-red-400 px-3 py-2">{err}</div>
            )}

            {loading && !report && (
                <div className="text-xs text-gray-500 italic px-3 py-4">Loading…</div>
            )}

            {report && report.cells.length === 0 && !loading && (
                <div className="text-xs text-gray-500 italic px-3 py-4">
                    No convert jobs in the selected window.
                </div>
            )}

            {report && report.cells.length > 0 && (
                <div className="overflow-auto">
                    <table className="text-xs border-collapse w-full">
                        <thead className="sticky top-0 bg-gray-900 z-10">
                            <tr className="text-left text-gray-300">
                                <Th align="center">…</Th>
                                <Th onClick={() => onHeaderClick("cell")}>cell{sortArrow("cell")}</Th>
                                <Th onClick={() => onHeaderClick("samples")} align="right">n{sortArrow("samples")}</Th>
                                <Th onClick={() => onHeaderClick("failures")} align="right">fail%{sortArrow("failures")}</Th>
                                <Th onClick={() => onHeaderClick("p50_duration")} align="right">p50 dur{sortArrow("p50_duration")}</Th>
                                <Th onClick={() => onHeaderClick("p95_duration")} align="right">p95 dur{sortArrow("p95_duration")}</Th>
                                <Th onClick={() => onHeaderClick("p95_rss")} align="right">p95 RSS{sortArrow("p95_rss")}</Th>
                                <Th align="right">max RSS</Th>
                                <Th onClick={() => onHeaderClick("rss_per_mb")} align="right">p95 RSS/MB{sortArrow("rss_per_mb")}</Th>
                                <Th align="right">cpu%</Th>
                                <Th align="right">avg input</Th>
                                <Th align="right">p50 out</Th>
                                <Th onClick={() => onHeaderClick("streaming")}>flags{sortArrow("streaming")}</Th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedCells.map((c) => {
                                const key = `${c.source_ext}::${c.target_format}`;
                                const isOpen = expanded === key;
                                return (
                                    <React.Fragment key={key}>
                                        <tr
                                            className={
                                                "border-t border-gray-800 hover:bg-gray-800/40 " +
                                                (c.streaming.is_candidate ? "bg-amber-950/20" : "")
                                            }
                                        >
                                            <td className="px-2 py-1 text-center">
                                                <button
                                                    type="button"
                                                    onClick={() => setExpanded(isOpen ? null : key)}
                                                    className="text-blue-400 hover:text-blue-300 font-mono"
                                                    title={isOpen ? "Hide hotspots" : "Show top functions by cumtime"}
                                                >
                                                    {isOpen ? "▾" : "▸"}
                                                </button>
                                            </td>
                                            <td className="px-2 py-1 font-mono text-gray-200">
                                                {c.source_ext} → {c.target_format}
                                            </td>
                                            <td className="px-2 py-1 text-right text-gray-300">{c.sample_count}</td>
                                            <td className={
                                                "px-2 py-1 text-right " +
                                                (c.failure_rate > 0.05 ? "text-red-400"
                                                    : c.failure_rate > 0 ? "text-amber-300"
                                                    : "text-gray-500")
                                            }>
                                                {fmtPct(c.failure_rate)}
                                            </td>
                                            <td className="px-2 py-1 text-right text-gray-300">{fmtMs(c.duration_ms_p50)}</td>
                                            <td className="px-2 py-1 text-right text-gray-300">{fmtMs(c.duration_ms_p95)}</td>
                                            <td className="px-2 py-1 text-right text-gray-300">{fmtKB(c.peak_rss_kb_p95)}</td>
                                            <td className="px-2 py-1 text-right text-gray-300">{fmtKB(c.peak_rss_max_kb)}</td>
                                            <td className="px-2 py-1 text-right text-gray-300">{fmtRatio(c.peak_rss_per_source_mb_p95)}</td>
                                            <td className={
                                                "px-2 py-1 text-right " +
                                                (c.cpu_fraction == null ? "text-gray-600"
                                                    : c.cpu_fraction < 0.3 ? "text-amber-300"
                                                    : "text-gray-300")
                                            } title={
                                                c.cpu_fraction == null
                                                    ? "no timing data"
                                                    : "(cpu_user_ms + cpu_sys_ms) / duration_ms"
                                            }>
                                                {fmtPct(c.cpu_fraction)}
                                            </td>
                                            <td className="px-2 py-1 text-right text-gray-500">{fmtBytes(c.read_bytes_avg)}</td>
                                            <td className="px-2 py-1 text-right text-gray-500">{fmtBytes(c.write_bytes_p50)}</td>
                                            <td className="px-2 py-1">
                                                <FlagsBadges cell={c} reasons={report.signal_reasons}/>
                                            </td>
                                        </tr>
                                        {isOpen && (
                                            <tr className="border-t border-gray-900 bg-gray-950">
                                                <td colSpan={13} className="px-3 py-2">
                                                    <HotspotsPanel
                                                        sourceExt={c.source_ext}
                                                        targetFormat={c.target_format}
                                                        sinceDays={windowDays}
                                                    />
                                                </td>
                                            </tr>
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

const Th: React.FC<{
    children: React.ReactNode;
    onClick?: () => void;
    align?: "left" | "right" | "center";
}> = ({children, onClick, align = "left"}) => (
    <th
        className={
            "px-2 py-1 border-b border-gray-700 font-medium whitespace-nowrap " +
            (align === "right" ? "text-right " : align === "center" ? "text-center " : "") +
            (onClick ? "cursor-pointer hover:text-blue-300" : "")
        }
        onClick={onClick}
    >
        {children}
    </th>
);

export default PerformanceTab;
