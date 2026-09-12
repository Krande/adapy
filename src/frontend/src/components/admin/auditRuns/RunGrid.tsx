import React, {useEffect, useMemo, useRef, useState} from "react";
import type {AuditRunJob} from "@/services/viewerApi";
import {
    METRIC_COLOR_BUCKETS,
    MetricKey,
    STATUS_COLOR,
    buildGrid,
    cellLabel,
    cellTooltip,
    cellValue,
    cellViewable,
    sourceFlags,
} from "./gridMetrics";

// Per-run drill-in — files × targets grid with cell coloring on
// pass/fail/cached and a metric switcher that recolors the same
// grid by peak_rss / elapsed_s / mem_per_input_mb / write_bytes.

const RunGrid: React.FC<{
    jobs: AuditRunJob[];
    metric: MetricKey;
    onCellHistory: (file: string, target: string) => void;
    onCellDetails: (file: string, target: string) => void;
    onCellOpen: (file: string, target: string) => void;
    onCellRerun: (file: string, target: string) => void;
}> = ({jobs, metric, onCellHistory, onCellDetails, onCellOpen, onCellRerun}) => {
    const grid = useMemo(() => buildGrid(jobs), [jobs]);

    // Right-click (desktop) / long-press (touch) context menu for a cell.
    const [menu, setMenu] = useState<{x: number; y: number; file: string; target: string} | null>(null);
    // Tapped quality-flag detail popover. The flag chip's ``title`` is a hover
    // tooltip, invisible on touch — a tap opens this so mobile can read it.
    const [flagInfo, setFlagInfo] = useState<{x: number; y: number; label: string; title: string} | null>(null);
    const longPress = useRef<number | null>(null);
    const openMenu = (x: number, y: number, file: string, target: string) =>
        setMenu({x, y, file, target});
    const cancelLongPress = () => {
        if (longPress.current != null) {
            window.clearTimeout(longPress.current);
            longPress.current = null;
        }
    };
    const onTouchStart = (e: React.TouchEvent, file: string, target: string) => {
        const t = e.touches[0];
        const x = t.clientX;
        const y = t.clientY;
        cancelLongPress();
        longPress.current = window.setTimeout(() => openMenu(x, y, file, target), 500);
    };
    // Close on any outside click / scroll / Escape while the menu is open.
    useEffect(() => {
        if (!menu) return;
        const close = () => setMenu(null);
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(null); };
        window.addEventListener("click", close);
        window.addEventListener("scroll", close, true);
        window.addEventListener("keydown", onKey);
        return () => {
            window.removeEventListener("click", close);
            window.removeEventListener("scroll", close, true);
            window.removeEventListener("keydown", onKey);
        };
    }, [menu]);

    // Close the flag-detail popover on any outside tap / scroll / Escape.
    useEffect(() => {
        if (!flagInfo) return;
        const close = () => setFlagInfo(null);
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFlagInfo(null); };
        window.addEventListener("click", close);
        window.addEventListener("scroll", close, true);
        window.addEventListener("keydown", onKey);
        return () => {
            window.removeEventListener("click", close);
            window.removeEventListener("scroll", close, true);
            window.removeEventListener("keydown", onKey);
        };
    }, [flagInfo]);

    // Per-source cross-format parity result (the dispatcher emits one
    // ``parity`` cell per source). The "Validation" metric colours every cell
    // in a source's row by this verdict — done = formats agree, error =
    // element-count mismatch — so you spot diverging sources at a glance.
    const parityBySource = useMemo(() => {
        const m = new Map<string, AuditRunJob>();
        for (const j of jobs) {
            if (j.target_format === "parity" && j.key) m.set(j.key, j);
        }
        return m;
    }, [jobs]);

    const validationClass = (file: string): string => {
        const p = parityBySource.get(file);
        if (!p) return "bg-gray-900 border-gray-800 text-gray-500";  // source has no parity cell
        return STATUS_COLOR[p.status ?? ""] || "bg-gray-800 border-gray-600 text-gray-300";
    };

    // For value-based metrics, colour each cell by its *magnitude*
    // on a single scale spanning the whole grid's min→max, so the
    // colour tracks the absolute value rather than the cell's rank.
    //
    // Earlier this bucketed by percentile (p25/p50/p90). That made
    // colour mean "how does this cell rank against its peers", which
    // collapsed wildly different magnitudes into the same bucket: in
    // a run dominated by sub-second cached cells the 90th percentile
    // sits below 1 s, so a 1 s cell and a 560 s cell both landed in
    // the red (>p90) bucket and looked identical. Mapping position in
    // [min, max] instead keeps 1 s green and 560 s red.
    //
    // Log scale because these metrics span orders of magnitude
    // (sub-second to minutes; KB to GB) — a linear map would crush
    // everything below the single slowest cell into the first bucket.
    // Only successful cells feed the scale; failed cells keep the
    // status palette so they stay visible regardless of metric.
    const scale = useMemo(() => {
        if (metric === "status" || metric === "validation") return null;
        let min = Infinity;
        let max = -Infinity;
        for (const j of jobs) {
            const status = j.status ?? "";
            if (status !== "done" && status !== "ok") continue;
            const v = cellValue(metric, j);
            if (v == null || v <= 0) continue;  // log scale needs positive values
            if (v < min) min = v;
            if (v > max) max = v;
        }
        if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
        const logMin = Math.log(min);
        return {logMin, logSpan: Math.log(max) - logMin};
    }, [jobs, metric]);

    const cellClass = (job: AuditRunJob | undefined): string => {
        if (!job) return "bg-gray-900 border-gray-800 text-gray-500";
        const status = job.status ?? "";
        // Non-OK statuses always render in their status palette so a
        // failed cell stays red regardless of which metric the user
        // selected — same recognisability as the pass/fail view.
        if (metric === "status" || status !== "done" && status !== "ok") {
            return STATUS_COLOR[status] || "bg-gray-900 border-gray-800 text-gray-500";
        }
        if (!scale) {
            return "bg-gray-900 border-gray-800 text-gray-500";
        }
        const v = cellValue(metric, job);
        if (v == null || v <= 0) {
            return "bg-gray-900 border-gray-800 text-gray-500";
        }
        // Position in [min, max] on a log scale → 0..1 → one of four
        // buckets. ``logSpan === 0`` means every cell shares the same
        // value (or there's only one); they all read as the low bucket.
        let bucket = 0;
        if (scale.logSpan > 0) {
            const t = (Math.log(v) - scale.logMin) / scale.logSpan;
            bucket = Math.min(3, Math.max(0, Math.floor(t * 4)));
        }
        return METRIC_COLOR_BUCKETS[bucket].cls;
    };

    if (grid.files.length === 0) {
        return (
            <div className="text-sm text-gray-400 italic px-4 py-6">
                No jobs in this run yet — the dispatcher may still be
                enumerating cells (background task).
            </div>
        );
    }

    return (
        // ``h-full`` so the parent's ``min-h-0 overflow-hidden`` can
        // clamp the inner overflow-auto. Without it the grid grows
        // to content height and mobile scrolls the page instead of
        // the table.
        <div className="h-full overflow-auto">
            <table className="text-xs border-collapse">
                <thead className="sticky top-0 bg-gray-900 z-10">
                    <tr>
                        <th className="text-left px-2 py-1 border-b border-gray-700 font-medium text-gray-300">
                            source
                        </th>
                        {grid.targets.map((t) => (
                            <th
                                key={t}
                                className="px-2 py-1 border-b border-gray-700 font-medium text-gray-300 text-center"
                            >
                                .{t}
                            </th>
                        ))}
                        <th className="px-2 py-1 border-b border-gray-700 font-medium text-gray-300 text-left" title="Per-source quality flags (OCC fallback, distorted triangles)">
                            flags
                        </th>
                    </tr>
                </thead>
                <tbody>
                    {grid.files.map((file) => (
                        <tr key={file} className="hover:bg-gray-800/40">
                            <td className="font-mono text-gray-300 px-2 py-1 border-b border-gray-800 max-w-xs truncate" title={file}>
                                {file}
                            </td>
                            {grid.targets.map((target) => {
                                const job = grid.cells.get(`${file}::${target}`);
                                const cls = metric === "validation" ? validationClass(file) : cellClass(job);
                                const label = cellLabel(metric, job);
                                return (
                                    <td
                                        key={target}
                                        className={`px-2 py-1 border ${cls} text-center min-w-[60px] cursor-context-menu select-none`}
                                        title={cellTooltip(job)}
                                        onContextMenu={(e) => {
                                            e.preventDefault();
                                            openMenu(e.clientX, e.clientY, file, target);
                                        }}
                                        onTouchStart={(e) => onTouchStart(e, file, target)}
                                        onTouchEnd={cancelLongPress}
                                        onTouchMove={cancelLongPress}
                                        onTouchCancel={cancelLongPress}
                                    >
                                        {label || "—"}
                                    </td>
                                );
                            })}
                            <td className="px-2 py-1 border-b border-gray-800 whitespace-nowrap">
                                {sourceFlags(grid.cells, grid.targets, file).map((f) => (
                                    <span
                                        key={f.key}
                                        className={`inline-block mr-1 px-1.5 py-0.5 rounded-sm border text-[10px] cursor-pointer ${f.cls}`}
                                        title={f.title}
                                        onClick={(e) => {
                                            // Tap opens the detail popover (mobile has no hover for ``title``).
                                            e.stopPropagation();
                                            const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                                            const x = Math.max(4, Math.min(r.left, window.innerWidth - 288));
                                            setFlagInfo({x, y: r.bottom + 4, label: f.label, title: f.title});
                                        }}
                                    >
                                        {f.label}
                                    </span>
                                ))}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
            {menu && (
                <div
                    className="fixed z-50 min-w-[160px] rounded-sm border border-gray-600 bg-gray-900 shadow-lg py-1 text-xs"
                    style={{left: menu.x, top: menu.y}}
                    // Keep clicks inside the menu from bubbling to the window
                    // close-listener before the item handler runs.
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="px-3 py-1 text-[10px] text-gray-500 font-mono truncate max-w-[240px]">
                        {menu.file} · .{menu.target}
                    </div>
                    {cellViewable(grid.cells.get(`${menu.file}::${menu.target}`)) && (
                        <button
                            type="button"
                            className="w-full text-left px-3 py-1 text-emerald-300 hover:bg-gray-700"
                            onClick={() => {
                                onCellOpen(menu.file, menu.target);
                                setMenu(null);
                            }}
                        >
                            Open in viewer ↗
                        </button>
                    )}
                    <button
                        type="button"
                        className="w-full text-left px-3 py-1 text-gray-200 hover:bg-gray-700"
                        onClick={() => {
                            onCellDetails(menu.file, menu.target);
                            setMenu(null);
                        }}
                    >
                        Show details
                    </button>
                    <button
                        type="button"
                        className="w-full text-left px-3 py-1 text-gray-200 hover:bg-gray-700"
                        onClick={() => {
                            onCellHistory(menu.file, menu.target);
                            setMenu(null);
                        }}
                    >
                        Show history
                    </button>
                    {menu.target !== "parity" && (
                        <button
                            type="button"
                            className="w-full text-left px-3 py-1 text-sky-300 hover:bg-gray-700"
                            onClick={() => {
                                onCellRerun(menu.file, menu.target);
                                setMenu(null);
                            }}
                        >
                            Rerun cell ↻
                        </button>
                    )}
                </div>
            )}
            {flagInfo && (
                <div
                    className="fixed z-50 max-w-[280px] rounded-sm border border-gray-600 bg-gray-900 shadow-lg p-2 text-xs"
                    style={{left: flagInfo.x, top: flagInfo.y}}
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="text-gray-200 font-medium mb-1">{flagInfo.label}</div>
                    <div className="text-gray-400 leading-snug">{flagInfo.title}</div>
                </div>
            )}
        </div>
    );
};

export default RunGrid;
