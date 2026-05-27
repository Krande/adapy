import React, {useCallback, useEffect, useMemo, useState} from "react";
import {viewerApi, AuditRun, AuditRunJob, Corpus} from "@/services/viewerApi";

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
    // Distinct capability tags advertised by every currently-online
    // worker (M2). Used to populate the pool picker so the operator
    // can't typo a tag — if a regression pod isn't registered yet,
    // its tag won't show up here either, which is the honest signal.
    const [capabilities, setCapabilities] = useState<string[]>([]);
    // Available corpora (M3). Audit sweeps against a curated corpus
    // are the release-gate flow; sweeping shared/user scopes is
    // mostly for ad-hoc debugging.
    const [corpora, setCorpora] = useState<Corpus[]>([]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const r = await viewerApi.adminListWorkers();
                if (cancelled) return;
                const tags = new Set<string>();
                for (const w of r.workers) {
                    if (!w.online) continue;
                    for (const c of w.capabilities || []) {
                        const v = c.trim().toLowerCase();
                        if (v) tags.add(v);
                    }
                }
                setCapabilities(Array.from(tags).sort());
            } catch {
                // No-op: the picker just falls back to "any" + a free
                // hint. Failure to list workers shouldn't break audit
                // dispatch — the operator can still type a tag.
            }
        })();
        (async () => {
            try {
                const r = await viewerApi.adminCorporaList();
                if (cancelled) return;
                setCorpora(r.corpora);
            } catch {
                // Non-fatal: scope picker still has shared / user:me.
            }
        })();
        return () => { cancelled = true; };
    }, []);

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
                <select
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-64"
                    title="Pick a corpus for release-gate sweeps, or a non-corpus scope for ad-hoc debugging."
                >
                    {corpora.length > 0 && (
                        <optgroup label="Corpora (release-gate)">
                            {corpora.map((c) => (
                                <option key={c.slug} value={`corpus:${c.slug}`}>
                                    corpus:{c.slug}
                                </option>
                            ))}
                        </optgroup>
                    )}
                    <optgroup label="Ad-hoc">
                        <option value="shared">shared</option>
                        <option value="user:me">user:me</option>
                    </optgroup>
                </select>
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Worker pool</span>
                <select
                    value={workerPool}
                    onChange={(e) => setWorkerPool(e.target.value)}
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-40"
                    title={
                        capabilities.length === 0
                            ? "No online workers found; pool restriction won't take effect"
                            : "Restrict the sweep to workers advertising this capability tag"
                    }
                >
                    <option value="">any pool</option>
                    {capabilities.map((c) => (
                        <option key={c} value={c}>{c}</option>
                    ))}
                </select>
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

    // ``md:`` breakpoint switches from stacked (mobile) to side-by-side
    // (desktop) — Tailwind's ``md`` is 768 px. Below md the history
    // list collapses out of view once a run is selected so the grid
    // gets full screen width; the "← back" button in the per-run
    // header restores the list.
    const showHistory = !selectedId;  // only matters on mobile

    return (
        <div className="flex flex-col h-full">
            <TriggerForm onCreated={loadRuns}/>

            <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
                {/* History list. Side-by-side w-80 on md+; full-width
                    on mobile, hidden once a run is selected.

                    Mobile scroll wiring: parent is ``flex-col``, so
                    this div needs ``flex-1 min-h-0`` to claim the
                    available column height AND let its inner
                    overflow-auto kick in. Without ``min-h-0`` flex
                    children default to ``min-height: auto`` which
                    refuses to shrink below content size — the page
                    ends up scrolling instead of the list. Desktop
                    reverts to a fixed ``md:w-80`` row child with
                    natural height from the row's overflow-hidden
                    parent. */}
                <div className={
                    "md:w-80 md:shrink-0 md:flex-none md:border-r md:border-b-0 " +
                    "flex-1 min-h-0 border-b border-gray-800 overflow-auto " +
                    (showHistory ? "block" : "hidden md:block")
                }>
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
                                                : run.status === "aborted" ? "text-orange-400"
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

                {/* Per-run grid. Hidden on mobile when no run is
                    selected so the history list owns the viewport. */}
                <div className={
                    "flex-1 min-h-0 flex-col overflow-hidden " +
                    (showHistory ? "hidden md:flex" : "flex")
                }>
                    {!selectedRun && (
                        <div className="hidden md:block text-xs text-gray-500 italic px-4 py-6">
                            Pick a run from the list to see its file × target grid.
                        </div>
                    )}
                    {selectedRun && (
                        <>
                            <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-3 flex-wrap">
                                <div className="flex items-center gap-2 min-w-0">
                                    {/* Mobile-only back link. On desktop
                                        the history list is always
                                        visible so this would be
                                        redundant. */}
                                    <button
                                        type="button"
                                        onClick={() => setSelectedId(null)}
                                        className="md:hidden text-sm text-blue-400 hover:text-blue-300 shrink-0"
                                        title="Back to run list"
                                    >
                                        ← list
                                    </button>
                                    <div className="text-xs text-gray-300 min-w-0">
                                        <div className="font-mono truncate">{selectedRun.scope}</div>
                                        <div className="text-gray-500">
                                            ok {selectedRun.ok} · failed {selectedRun.failed} ·
                                            skipped {selectedRun.skipped} · total {selectedRun.total}
                                        </div>
                                        <IssueBotStatus
                                            run={selectedRun}
                                            onChanged={() => selectedId && loadDetail(selectedId)}
                                        />
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 shrink-0">
                                    {selectedRun.status === "running" && (
                                        <CancelRunButton
                                            run={selectedRun}
                                            onCancelled={() => {
                                                void loadRuns();
                                                if (selectedId) void loadDetail(selectedId);
                                            }}
                                        />
                                    )}
                                    <label className="text-xs text-gray-300 flex items-center gap-2">
                                        <span className="hidden sm:inline">Color cells by:</span>
                                        <span className="sm:hidden">Metric:</span>
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
