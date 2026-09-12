import React from "react";
import type {AuditRun} from "@/services/viewerApi";
import {RuntimeMode, fmtRunDuration} from "./gridMetrics";

// History list. Side-by-side w-80 on md+; full-width on mobile, hidden once a
// run is selected.
//
// Mobile scroll wiring: parent is ``flex-col``, so this div needs ``flex-1
// min-h-0`` to claim the available column height AND let its inner
// overflow-auto kick in. Without ``min-h-0`` flex children default to
// ``min-height: auto`` which refuses to shrink below content size — the page
// ends up scrolling instead of the list. Desktop reverts to a fixed
// ``md:w-80`` row child with natural height from the row's overflow-hidden
// parent.
const RunList: React.FC<{
    runs: AuditRun[];
    selectedId: string | null;
    onSelect: (runId: string) => void;
    /** Only matters on mobile: the list hides once a run is selected. */
    visible: boolean;
    listError: string | null;
    runtimeMode: RuntimeMode;
    onRuntimeMode: (m: RuntimeMode) => void;
    toastHidden: boolean;
    onToggleToast: () => void;
}> = ({runs, selectedId, onSelect, visible, listError, runtimeMode, onRuntimeMode, toastHidden, onToggleToast}) => (
    <div className={
        "md:w-80 md:shrink-0 md:flex-none md:border-r md:border-b-0 " +
        "flex-1 min-h-0 border-b border-gray-800 overflow-auto " +
        (visible ? "block" : "hidden md:block")
    }>
        {/* Overview toggle: show each run's runtime as the sum of its
            cell times or as active wall clock. Both are relevant —
            cells = compute cost, wall = time waited. Sticky so it
            stays put while the list scrolls. */}
        <div className="sticky top-0 z-10 flex items-center justify-between gap-2 px-3 py-1.5 border-b border-gray-800 bg-gray-900/80 backdrop-blur text-[11px] text-gray-400">
            <div className="flex items-center gap-1.5">
                <span>Runtime</span>
                <div className="inline-flex rounded-sm border border-gray-700 overflow-hidden">
                    <button
                        type="button"
                        onClick={() => onRuntimeMode("cells")}
                        className={"px-2 py-0.5 " + (runtimeMode === "cells"
                            ? "bg-blue-700 text-white" : "text-gray-300 hover:bg-gray-800")}
                        title="Sum of every cell's own runtime — the real compute cost, immune to worker parallelism and single-cell re-runs."
                    >
                        Σ cells
                    </button>
                    <button
                        type="button"
                        onClick={() => onRuntimeMode("wall")}
                        className={"px-2 py-0.5 border-l border-gray-700 " + (runtimeMode === "wall"
                            ? "bg-blue-700 text-white" : "text-gray-300 hover:bg-gray-800")}
                        title="Active wall-clock time (finished − started − idle) — how long the run actually took to watch."
                    >
                        wall
                    </button>
                </div>
            </div>
            <button
                type="button"
                onClick={onToggleToast}
                className={"px-2 py-0.5 rounded-sm border " + (toastHidden
                    ? "border-gray-700 text-gray-400 hover:bg-gray-800"
                    : "border-blue-700 bg-blue-900/40 text-blue-200 hover:bg-blue-900/60")}
                title="Show/hide the ambient 'audit sweep in progress' toast over the viewer."
            >
                {toastHidden ? "◌ toast off" : "● toast on"}
            </button>
        </div>
        {listError && (
            <div className="text-xs text-red-400 px-3 py-2">{listError}</div>
        )}
        {runs.length === 0 && !listError && (
            <div className="text-xs text-gray-500 italic px-3 py-4">
                No audit runs yet. Use the form above to start one.
            </div>
        )}
        <ul className="text-xs">
            {runs.map((run) => {
                const active = run.id === selectedId;
                const pct = run.total > 0
                    ? Math.round(100 * (run.ok + run.failed + run.skipped) / run.total)
                    : 0;
                return (
                    <li
                        key={run.id}
                        onClick={() => onSelect(run.id)}
                        className={
                            "px-3 py-2 border-b border-gray-800 cursor-pointer " +
                            (active
                                ? "bg-blue-900/40"
                                : "hover:bg-gray-800/40")
                        }
                    >
                        <div className="flex justify-between items-baseline">
                            <span className="font-mono text-gray-200 truncate">
                                {run.seq != null && (
                                    <span className="text-gray-500 mr-1">#{run.seq}</span>
                                )}
                                {run.scope}
                            </span>
                            <span className={
                                "ml-2 text-[10px] shrink-0 " +
                                (run.status === "running" ? "text-blue-300"
                                    : run.status === "aborted" ? "text-orange-400"
                                    : run.failed > 0 ? "text-red-400"
                                    : "text-emerald-400")
                            }>
                                {run.status}
                            </span>
                        </div>
                        <div className="text-gray-400 mt-0.5 flex justify-between">
                            <span>{run.ok + run.failed + run.skipped} / {run.total}</span>
                            <span title={runtimeMode === "cells" ? "sum of cell runtimes" : "active wall clock"}>
                                {fmtRunDuration(run, runtimeMode)}
                            </span>
                        </div>
                        {run.total > 0 && (
                            <div className="h-1 bg-gray-700 rounded-sm overflow-hidden mt-1">
                                <div
                                    className={
                                        "h-full transition-all " +
                                        (run.failed > 0 ? "bg-red-500"
                                            : run.status === "finished" ? "bg-emerald-500"
                                            : "bg-blue-500")
                                    }
                                    style={{width: `${Math.max(pct, 4)}%`}}
                                />
                            </div>
                        )}
                        {run.note && (
                            <div className="text-gray-500 text-[10px] mt-1 truncate" title={run.note}>
                                {run.note}
                            </div>
                        )}
                    </li>
                );
            })}
        </ul>
    </div>
);

export default RunList;
