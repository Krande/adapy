import React, {useCallback, useEffect, useMemo, useState} from "react";
import {viewerApi, AuditRun, AuditRunJob} from "@/services/viewerApi";

// Admin tab — kick off regression sweeps across the converter matrix
// and drill into per-cell results. Layer 1 of the audit panel from
// plan/v2/notes_admin_audit_panel.md:
//
//   * "Run audit" form — pick a scope (M3 will add a corpus picker)
//     and an optional worker pool, fire one POST to /admin/audit/runs.
//   * History list — recent runs in reverse-chronological order,
//     polled every 5 s so in-flight runs visibly advance their
//     counters without manual refresh.
//   * Per-run drill-in — files × targets grid with cell coloring on
//     pass/fail/cached and a metric switcher that recolors the same
//     grid by peak_rss / elapsed_s / mem_per_input_mb / write_bytes.

type MetricKey = "status" | "peak_rss_kb" | "duration_ms" | "mem_per_mb" | "write_bytes";

const METRIC_LABELS: Record<MetricKey, string> = {
    status: "Pass / fail",
    peak_rss_kb: "Peak RSS",
    duration_ms: "Elapsed",
    mem_per_mb: "RSS / source MB",
    write_bytes: "Output size",
};

const POLL_INTERVAL_MS = 5000;

const STATUS_COLOR: Record<string, string> = {
    done: "bg-emerald-900/60 border-emerald-600 text-emerald-100",
    ok: "bg-emerald-900/60 border-emerald-600 text-emerald-100",
    error: "bg-red-900/60 border-red-600 text-red-100",
    failed: "bg-red-900/60 border-red-600 text-red-100",
    queued: "bg-amber-900/40 border-amber-600 text-amber-100",
    running: "bg-blue-900/40 border-blue-600 text-blue-100",
    cancelled: "bg-gray-800 border-gray-600 text-gray-300",
    skipped: "bg-gray-800 border-gray-600 text-gray-400",
};

function fmtBytes(n: number | null | undefined): string {
    if (n == null) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtMs(n: number | null | undefined): string {
    if (n == null) return "—";
    if (n < 1000) return `${n} ms`;
    return `${(n / 1000).toFixed(1)} s`;
}

function fmtRunDuration(run: AuditRun): string {
    if (!run.started_at) return "—";
    const start = new Date(run.started_at).getTime();
    const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
    const ms = Math.max(0, end - start);
    if (ms < 60_000) return `${(ms / 1000).toFixed(0)}s`;
    if (ms < 3600_000) return `${(ms / 60_000).toFixed(0)}m`;
    return `${(ms / 3600_000).toFixed(1)}h`;
}

// Build the (file × target) grid from a flat job list. One row per
// source file, one column per target_format. Empty cells are
// targets the registry didn't list for that source's extension.
function buildGrid(jobs: AuditRunJob[]): {
    files: string[];
    targets: string[];
    cells: Map<string, AuditRunJob>;  // key: `${file}::${target}`
} {
    const fileSet = new Set<string>();
    const targetSet = new Set<string>();
    const cells = new Map<string, AuditRunJob>();
    for (const j of jobs) {
        if (!j.key || !j.target_format) continue;
        fileSet.add(j.key);
        targetSet.add(j.target_format);
        cells.set(`${j.key}::${j.target_format}`, j);
    }
    return {
        files: Array.from(fileSet).sort(),
        targets: Array.from(targetSet).sort(),
        cells,
    };
}

function cellLabel(metric: MetricKey, job: AuditRunJob | undefined, sourceSizeMb: number | null): string {
    if (!job) return "";
    if (metric === "status") return job.status ?? "";
    if (metric === "peak_rss_kb") return fmtBytes(job.peak_rss_kb ? job.peak_rss_kb * 1024 : null);
    if (metric === "duration_ms") return fmtMs(job.duration_ms);
    if (metric === "write_bytes") return fmtBytes(job.write_bytes);
    if (metric === "mem_per_mb") {
        if (!job.peak_rss_kb || !sourceSizeMb || sourceSizeMb <= 0) return "—";
        const ratio = (job.peak_rss_kb / 1024) / sourceSizeMb;
        return `${ratio.toFixed(1)}×`;
    }
    return "";
}

function cellTooltip(job: AuditRunJob | undefined): string {
    if (!job) return "no job";
    const parts: string[] = [];
    if (job.status) parts.push(`status: ${job.status}`);
    if (job.duration_ms != null) parts.push(`elapsed: ${fmtMs(job.duration_ms)}`);
    if (job.peak_rss_kb != null) parts.push(`peak rss: ${fmtBytes(job.peak_rss_kb * 1024)}`);
    if (job.read_bytes != null) parts.push(`read: ${fmtBytes(job.read_bytes)}`);
    if (job.write_bytes != null) parts.push(`write: ${fmtBytes(job.write_bytes)}`);
    if (job.error) parts.push(`error: ${job.error.slice(0, 200)}`);
    return parts.join("\n");
}

const RunGrid: React.FC<{
    jobs: AuditRunJob[];
    metric: MetricKey;
}> = ({jobs, metric}) => {
    const grid = useMemo(() => buildGrid(jobs), [jobs]);

    if (grid.files.length === 0) {
        return (
            <div className="text-sm text-gray-400 italic px-4 py-6">
                No jobs in this run yet — the dispatcher may still be
                enumerating cells (background task).
            </div>
        );
    }

    return (
        <div className="overflow-auto">
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
                                const status = job?.status ?? "";
                                const cls = STATUS_COLOR[status] || "bg-gray-900 border-gray-800 text-gray-500";
                                const label = cellLabel(metric, job, null);
                                return (
                                    <td
                                        key={target}
                                        className={`px-2 py-1 border ${cls} text-center min-w-[60px] cursor-help`}
                                        title={cellTooltip(job)}
                                    >
                                        {label || "—"}
                                    </td>
                                );
                            })}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

const TriggerForm: React.FC<{onCreated: () => void}> = ({onCreated}) => {
    const [scope, setScope] = useState("shared");
    const [workerPool, setWorkerPool] = useState("");
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const onSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunCreate({
                scope,
                worker_pool: workerPool.trim() || null,
                note: note.trim() || null,
            });
            setNote("");
            onCreated();
        } catch (e) {
            setErr((e as Error).message || "audit run create failed");
        } finally {
            setBusy(false);
        }
    }, [scope, workerPool, note, onCreated]);

    return (
        <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900/40">
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Scope</span>
                <input
                    type="text"
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                    placeholder="shared | user:me | project:<id>"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-64"
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Worker pool <span className="text-gray-500">(optional)</span></span>
                <input
                    type="text"
                    value={workerPool}
                    onChange={(e) => setWorkerPool(e.target.value)}
                    placeholder="audit (M2)"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-40"
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1 flex-1 min-w-[200px]">
                <span>Note <span className="text-gray-500">(optional)</span></span>
                <input
                    type="text"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="release v0.8 dry run"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                />
            </label>
            <button
                type="submit"
                disabled={busy}
                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm h-[30px]"
            >
                {busy ? "Starting…" : "Run audit"}
            </button>
            {err && (
                <div className="w-full text-xs text-red-400" role="alert">{err}</div>
            )}
        </form>
    );
};

const AuditRunsTab: React.FC = () => {
    const [runs, setRuns] = useState<AuditRun[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [selectedRun, setSelectedRun] = useState<AuditRun | null>(null);
    const [selectedJobs, setSelectedJobs] = useState<AuditRunJob[]>([]);
    const [metric, setMetric] = useState<MetricKey>("status");
    const [listError, setListError] = useState<string | null>(null);
    const [detailError, setDetailError] = useState<string | null>(null);

    const loadRuns = useCallback(async () => {
        try {
            const r = await viewerApi.adminAuditRunsList({limit: 30});
            setRuns(r.runs);
            setListError(null);
        } catch (e) {
            setListError((e as Error).message || "failed to load audit runs");
        }
    }, []);

    const loadDetail = useCallback(async (runId: string) => {
        try {
            const r = await viewerApi.adminAuditRunGet(runId);
            setSelectedRun(r.run);
            setSelectedJobs(r.jobs);
            setDetailError(null);
        } catch (e) {
            setDetailError((e as Error).message || "failed to load run");
        }
    }, []);

    useEffect(() => { void loadRuns(); }, [loadRuns]);

    // Poll while any visible run is still running — saves the user
    // hitting refresh while the dispatcher's BackgroundTask fills in
    // ``total`` and workers stream their outcomes.
    useEffect(() => {
        const anyRunning = runs.some((r) => r.status === "running")
            || (selectedRun?.status === "running");
        if (!anyRunning) return;
        const id = window.setInterval(() => {
            void loadRuns();
            if (selectedId) void loadDetail(selectedId);
        }, POLL_INTERVAL_MS);
        return () => window.clearInterval(id);
    }, [runs, selectedRun, selectedId, loadRuns, loadDetail]);

    const onSelectRun = useCallback((runId: string) => {
        setSelectedId(runId);
        void loadDetail(runId);
    }, [loadDetail]);

    return (
        <div className="flex flex-col h-full">
            <TriggerForm onCreated={loadRuns}/>

            <div className="flex-1 flex overflow-hidden">
                {/* Left: history list */}
                <div className="w-80 shrink-0 border-r border-gray-800 overflow-auto">
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
                                    onClick={() => onSelectRun(run.id)}
                                    className={
                                        "px-3 py-2 border-b border-gray-800 cursor-pointer " +
                                        (active
                                            ? "bg-blue-900/40"
                                            : "hover:bg-gray-800/40")
                                    }
                                >
                                    <div className="flex justify-between items-baseline">
                                        <span className="font-mono text-gray-200 truncate">
                                            {run.scope}
                                        </span>
                                        <span className={
                                            "ml-2 text-[10px] shrink-0 " +
                                            (run.status === "running" ? "text-blue-300"
                                                : run.failed > 0 ? "text-red-400"
                                                : "text-emerald-400")
                                        }>
                                            {run.status}
                                        </span>
                                    </div>
                                    <div className="text-gray-400 mt-0.5 flex justify-between">
                                        <span>{run.ok + run.failed + run.skipped} / {run.total}</span>
                                        <span>{fmtRunDuration(run)}</span>
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

                {/* Right: per-run grid */}
                <div className="flex-1 flex flex-col overflow-hidden">
                    {!selectedRun && (
                        <div className="text-xs text-gray-500 italic px-4 py-6">
                            Pick a run from the list to see its file × target grid.
                        </div>
                    )}
                    {selectedRun && (
                        <>
                            <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-3 flex-wrap">
                                <div className="text-xs text-gray-300">
                                    <div className="font-mono">{selectedRun.scope}</div>
                                    <div className="text-gray-500">
                                        ok {selectedRun.ok} · failed {selectedRun.failed} ·
                                        skipped {selectedRun.skipped} · total {selectedRun.total}
                                    </div>
                                </div>
                                <label className="text-xs text-gray-300 flex items-center gap-2">
                                    Color cells by:
                                    <select
                                        value={metric}
                                        onChange={(e) => setMetric(e.target.value as MetricKey)}
                                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-xs text-gray-100"
                                    >
                                        {(Object.keys(METRIC_LABELS) as MetricKey[]).map((k) => (
                                            <option key={k} value={k}>{METRIC_LABELS[k]}</option>
                                        ))}
                                    </select>
                                </label>
                            </div>
                            {detailError && (
                                <div className="text-xs text-red-400 px-3 py-2">{detailError}</div>
                            )}
                            <div className="flex-1 overflow-hidden">
                                <RunGrid jobs={selectedJobs} metric={metric}/>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default AuditRunsTab;
