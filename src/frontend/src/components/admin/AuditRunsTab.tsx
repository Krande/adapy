import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {viewerApi, AuditRun, AuditRunJob, AuditCellHistoryRow, Corpus} from "@/services/viewerApi";
import {runWasmAuditSweep, WasmSweepProgress} from "@/services/audit/wasmSweep";
import {runtime} from "@/runtime/config";
import {useAuditToastStore} from "@/state/auditToastStore";
import {view_in_3d} from "@/utils/scene/handlers/view_in_3d";

// Synthetic worker-pool value routing a run to the in-browser WASM engine.
const WASM_POOL = "wasm";

// Admin tab — kick off regression sweeps across the converter matrix
// and drill into per-cell results. Layer 1 of the audit panel from
// the admin audit-panel design notes:
//
//   * "Run audit" form — pick a scope (M3 will add a corpus picker)
//     and an optional worker pool, fire one POST to /admin/audit/runs.
//   * History list — recent runs in reverse-chronological order,
//     polled every 5 s so in-flight runs visibly advance their
//     counters without manual refresh.
//   * Per-run drill-in — files × targets grid with cell coloring on
//     pass/fail/cached and a metric switcher that recolors the same
//     grid by peak_rss / elapsed_s / mem_per_input_mb / write_bytes.

type MetricKey = "status" | "validation" | "peak_rss_kb" | "duration_ms" | "mem_per_mb" | "write_bytes";

const METRIC_LABELS: Record<MetricKey, string> = {
    status: "Pass / fail",
    validation: "Validation",
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

type RuntimeMode = "cells" | "wall";

// Two equally-relevant views of a run's runtime, switched by the overview toggle:
//   "cells" — SUM of every cell's own duration_ms: the real compute cost. Immune
//             to worker parallelism and to a single-cell re-run reopening the run
//             (which would inflate wall clock with the idle gap since the first
//             run). Recomputes server-side whenever a cell's row changes.
//   "wall"  — active wall clock (finished−started−idle): how long the operator
//             actually waited, which parallelism compresses below the cell sum.
// "cells" falls back to wall clock for older runs / in-flight rows with no sum yet.
function fmtRunDuration(run: AuditRun, mode: RuntimeMode): string {
    const sum = run.cells_duration_ms;
    let ms: number;
    if (mode === "cells" && sum != null && sum > 0) {
        ms = sum;
    } else {
        if (!run.started_at) return "—";
        const start = new Date(run.started_at).getTime();
        const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
        ms = Math.max(0, end - start - (run.idle_ms ?? 0));
    }
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

// Numeric value of a cell under the currently-selected metric. Used
// both for the cell label AND for the value-based color gradient
// across the grid. Returns null when the cell has no data for that
// metric (queued / cached / failed cells often lack RSS samples).
function cellValue(metric: MetricKey, job: AuditRunJob | undefined): number | null {
    if (!job) return null;
    if (metric === "peak_rss_kb") return job.peak_rss_kb ?? null;
    if (metric === "duration_ms") return job.duration_ms ?? null;
    if (metric === "write_bytes") return job.write_bytes ?? null;
    if (metric === "mem_per_mb") {
        // RSS / source MB. Use ``read_bytes`` as the source size
        // proxy — that's what the worker pulled in from storage, and
        // the only per-job size we have in the AuditRunJob shape.
        // <1 KB inputs floor at 0.001 MB to avoid divide-by-zero
        // blowing up the gradient.
        if (job.peak_rss_kb == null || job.read_bytes == null) return null;
        const sourceMb = Math.max(job.read_bytes / 1024 / 1024, 0.001);
        return (job.peak_rss_kb / 1024) / sourceMb;
    }
    return null;
}

function cellLabel(metric: MetricKey, job: AuditRunJob | undefined): string {
    if (!job) return "";
    if (metric === "status" || metric === "validation") return job.status ?? "";
    const v = cellValue(metric, job);
    if (v == null) return "—";
    if (metric === "peak_rss_kb") return fmtBytes(v * 1024);
    if (metric === "duration_ms") return fmtMs(v);
    if (metric === "write_bytes") return fmtBytes(v);
    if (metric === "mem_per_mb") return `${v.toFixed(1)}×`;
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
    if (job.worker_image_tag) parts.push(`worker: ${job.worker_image_tag}`);
    if (job.error) parts.push(`error: ${job.error.slice(0, 200)}`);
    return parts.join("\n");
}

// 3-stop colour palette (low / mid / high) for value-based metric
// shading. Distinct from the status palette so the user can tell at
// a glance whether they're reading "did this conversion pass?"
// (status colours) or "how heavy was it?" (gradient colours). Cells
// with non-OK status keep the status palette regardless of metric
// — a failed cell is still failed when you're looking at RSS.
const METRIC_COLOR_BUCKETS: {cls: string}[] = [
    {cls: "bg-emerald-900/40 border-emerald-700 text-emerald-100"},  // fast / light
    {cls: "bg-amber-900/40 border-amber-700 text-amber-100"},        // medium
    {cls: "bg-orange-900/60 border-orange-600 text-orange-100"},     // heavy
    {cls: "bg-red-900/60 border-red-600 text-red-100"},              // outlier
];

// The derived-blob key a cell produced. Mirrors the server's
// derived_key_for convention: _derived/<source>.<target>, except a glb
// target of a source that is already a .glb has no derivation.
function cellDerivedKey(file: string, target: string): string {
    if (target === "glb" && file.toLowerCase().endsWith(".glb")) return file;
    return `_derived/${file}.${target}`;
}

// Whether a cell's product can be opened in the 3D viewer. The viewer mounts
// GLB directly and converts any target the converter can turn into GLB
// (ifc/step/xml/...); parity has no artifact, and a non-done cell has nothing
// cached to open. Mirrors ConversionRow's canPreview gate.
function cellViewable(job: AuditRunJob | undefined): boolean {
    if (!job || !job.key || !job.target_format) return false;
    if (!(job.status === "done" || job.status === "ok")) return false;
    const t = job.target_format;
    if (t === "glb") return true;
    if (t === "parity") return false;
    return runtime.conversionTargetsFor(t).includes("glb");
}

type CellFlag = {key: string; label: string; title: string; cls: string};

// Per-source quality flags, aggregated across the row's cells' convert_meta. Mirrors the
// PerformanceTab "streaming" pill idea: a compact badge per detected issue.
//   occ_fallback — the NGEOM/libtess2 (OCC-free) tessellation silently fell back to OCC.
//   distorted    — a converted mesh has heavily distorted (degenerate/sliver) triangles.
function sourceFlags(cells: Map<string, AuditRunJob>, targets: string[], file: string): CellFlag[] {
    let occ = 0;
    let distorted = 0;
    let dropped = 0;
    for (const t of targets) {
        const cm = cells.get(`${file}::${t}`)?.convert_meta;
        if (!cm) continue;
        occ += cm.occ_fallback?.count ?? 0;
        distorted += cm.mesh_flags?.distorted_tris ?? 0;
        dropped = Math.max(dropped, cm.geom_health?.dropped_faces ?? 0);
    }
    const flags: CellFlag[] = [];
    if (dropped > 0)
        flags.push({
            key: "dropped",
            label: "dropped faces",
            title: `${dropped} face(s) with a trim boundary tessellated to zero triangles — silently dropped geometry (e.g. a swept/extruded surface the kernel couldn't mesh)`,
            cls: "bg-red-900/50 border-red-700 text-red-200",
        });
    if (occ > 0)
        flags.push({
            key: "occ",
            label: "occ fallback",
            title: `${occ} object(s) fell back from the libtess2 stream kernel to OCC`,
            cls: "bg-amber-900/50 border-amber-700 text-amber-200",
        });
    if (distorted > 0)
        flags.push({
            key: "distorted",
            label: "distorted tris",
            title: `${distorted} heavily distorted (degenerate/sliver) triangle(s) in a converted mesh`,
            cls: "bg-red-900/50 border-red-700 text-red-200",
        });
    return flags;
}

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
                                        className={`inline-block mr-1 px-1.5 py-0.5 rounded-sm border text-[10px] ${f.cls}`}
                                        title={f.title}
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
        </div>
    );
};

const TriggerForm: React.FC<{onCreated: () => void}> = ({onCreated}) => {
    const [scope, setScope] = useState("shared");
    const [workerPool, setWorkerPool] = useState("");
    const [note, setNote] = useState("");
    const [forceRebuild, setForceRebuild] = useState(false);
    // When on, the run auto-fires a follow-up validate_only parity pass once it
    // finishes (replaces the old standalone "Run validation" button).
    const [autoValidate, setAutoValidate] = useState(false);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    // In-browser sweep progress (WASM pool only); null when idle.
    const [sweep, setSweep] = useState<WasmSweepProgress | null>(null);
    const [sweepErr, setSweepErr] = useState<string | null>(null);
    const isWasmPool = workerPool.trim().toLowerCase() === WASM_POOL;
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

    const createRun = useCallback(async () => {
        setBusy(true);
        setErr(null);
        setSweepErr(null);
        try {
            const run = await viewerApi.adminAuditRunCreate({
                scope,
                worker_pool: workerPool.trim() || null,
                note: note.trim() || null,
                force_rebuild: forceRebuild,
                auto_validate: autoValidate,
            });
            setNote("");
            onCreated();
            // A WASM run is created server-side but dispatches nothing — the
            // browser drives its cells here. Fire-and-forget: the runs list
            // polls and reflects progress from the audit rows the sweep
            // writes; we also surface a local progress line.
            if (isWasmPool) {
                setSweep({total: 0, completed: 0, current: null});
                void runWasmAuditSweep(scope, run.id, (p) => setSweep(p))
                    .then(() => onCreated())
                    .catch((e) => setSweepErr((e as Error).message || "wasm sweep failed"))
                    .finally(() => setSweep(null));
            }
        } catch (e) {
            setErr((e as Error).message || "audit run create failed");
        } finally {
            setBusy(false);
        }
    }, [scope, workerPool, note, forceRebuild, autoValidate, onCreated, isWasmPool]);

    const onSubmit = useCallback((e: React.FormEvent) => {
        e.preventDefault();
        void createRun();
    }, [createRun]);

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
                    <option value={WASM_POOL}>WASM (in-browser)</option>
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
            <label
                className="text-xs text-gray-300 flex items-center gap-1 h-[30px] mt-auto select-none"
                title={
                    "Skip the dispatcher's cached-blob short-circuit so every cell " +
                    "actually re-converts. Use for perf measurements; a second run " +
                    "against the same scope otherwise short-circuits ~80% of cells " +
                    "against prior outputs."
                }
            >
                <input
                    type="checkbox"
                    checked={forceRebuild}
                    onChange={(e) => setForceRebuild(e.target.checked)}
                    className="accent-blue-600"
                />
                <span>Force rebuild</span>
            </label>
            <label
                className="text-xs text-gray-300 flex items-center gap-1 h-[30px] mt-auto select-none"
                title={
                    isWasmPool
                        ? "Auto-validate runs on the worker pool only; ignored for in-browser (WASM) sweeps."
                        : "After this run finishes, automatically start a validation pass " +
                          "(cross-format visual-parity per source) for the same scope."
                }
            >
                <input
                    type="checkbox"
                    checked={autoValidate}
                    disabled={isWasmPool}
                    onChange={(e) => setAutoValidate(e.target.checked)}
                    className="accent-teal-600 disabled:opacity-40"
                />
                <span className={isWasmPool ? "opacity-40" : undefined}>Validate after</span>
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
            {isWasmPool && (
                <div className="w-full text-xs text-amber-300/90">
                    In-browser sweep: runs in this tab via the WASM engine — keep it open until it finishes.
                    Reopening resumes (completed cells are skipped); non-WASM cells (e.g. <code>.odb</code>,
                    non-GLB targets) are recorded as skipped.
                </div>
            )}
            {sweep && (
                <div className="w-full text-xs text-gray-300" role="status">
                    Sweeping {sweep.completed}/{sweep.total}
                    {sweep.current ? <> — <span className="font-mono text-gray-400">{sweep.current}</span></> : null}
                </div>
            )}
            {sweepErr && (
                <div className="w-full text-xs text-red-400" role="alert">sweep: {sweepErr}</div>
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

const ReDispatchButton: React.FC<{
    run: AuditRun;
    onDispatched: () => void;
}> = ({run, onDispatched}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const onClick = async () => {
        if (!window.confirm(
            `Re-run this audit against "${run.scope}"? A new run is created with the same scope, pool and settings.`,
        )) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunReDispatch(run.id);
            onDispatched();
        } catch (e) {
            setErr((e as Error).message || "re-run failed");
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
                className="text-xs px-2 py-1 border border-blue-700 text-blue-300 hover:bg-blue-900/30 rounded-sm disabled:opacity-50"
                title="Create a new audit run with this run's scope / pool / settings."
            >
                {busy ? "Starting…" : "Re-run audit"}
            </button>
            {err && <span className="text-[11px] text-red-400" role="alert">{err}</span>}
        </div>
    );
};

// Kick off a cross-format parity validation pass on a finished run. The cells
// are appended to *this* run (it reopens to 'running' until they land), not a
// new run. Dispatched at most once per run — the button disables once a
// validation has already run (via the toggle or a prior click).
const ValidateRunButton: React.FC<{
    run: AuditRun;
    onValidated: () => void;
}> = ({run, onValidated}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const alreadyValidated = !!run.auto_validate_dispatched_at;
    const onClick = async () => {
        if (!window.confirm(
            `Run validation on "${run.scope}"? Cross-format parity cells are appended to this run.`,
        )) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunValidate(run.id);
            onValidated();
        } catch (e) {
            setErr((e as Error).message || "validation failed");
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="flex items-center gap-2">
            <button
                type="button"
                onClick={onClick}
                disabled={busy || alreadyValidated}
                className="text-xs px-2 py-1 border border-teal-700 text-teal-300 hover:bg-teal-900/30 rounded-sm disabled:opacity-50 disabled:cursor-not-allowed"
                title={
                    alreadyValidated
                        ? "Validation already dispatched for this run."
                        : "Append a cross-format parity validation pass to this run."
                }
            >
                {busy ? "Starting…" : alreadyValidated ? "Validated" : "Validate"}
            </button>
            {err && <span className="text-[11px] text-red-400" role="alert">{err}</span>}
        </div>
    );
};

// Delete a finished/aborted run and its audit_log rows (parity cascades).
const DeleteRunButton: React.FC<{
    run: AuditRun;
    onDeleted: () => void;
}> = ({run, onDeleted}) => {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const label = run.seq != null ? `#${run.seq}` : run.scope;
    const onClick = async () => {
        if (!window.confirm(
            `Delete audit run ${label} ("${run.scope}")? Its results are removed permanently.`,
        )) {
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            await viewerApi.adminAuditRunDelete(run.id);
            onDeleted();
        } catch (e) {
            setErr((e as Error).message || "delete failed");
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
                className="text-xs px-2 py-1 border border-red-800 text-red-300 hover:bg-red-900/30 rounded-sm disabled:opacity-50"
                title="Delete this run and its results."
            >
                {busy ? "Deleting…" : "Delete"}
            </button>
            {err && <span className="text-[11px] text-red-400" role="alert">{err}</span>}
        </div>
    );
};

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
                        <table className="text-xs border-collapse w-full">
                            <thead className="text-gray-400">
                                <tr>
                                    <th className="text-left px-2 py-1 border-b border-gray-700">when</th>
                                    <th className="text-left px-2 py-1 border-b border-gray-700">status</th>
                                    <th className="text-right px-2 py-1 border-b border-gray-700">dur</th>
                                    <th className="text-right px-2 py-1 border-b border-gray-700">peak RSS</th>
                                    <th className="text-left px-2 py-1 border-b border-gray-700">worker</th>
                                    <th className="text-left px-2 py-1 border-b border-gray-700">error</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((h) => (
                                    <tr key={h.id} className="hover:bg-gray-800/40">
                                        <td className="px-2 py-1 border-b border-gray-800 text-gray-300 whitespace-nowrap">
                                            {h.ts ? new Date(h.ts).toLocaleString() : "—"}
                                        </td>
                                        <td className="px-2 py-1 border-b border-gray-800 text-gray-200">{h.status}</td>
                                        <td className="px-2 py-1 border-b border-gray-800 text-right text-gray-300">
                                            {h.duration_ms != null ? `${(h.duration_ms / 1000).toFixed(1)}s` : "—"}
                                        </td>
                                        <td className="px-2 py-1 border-b border-gray-800 text-right text-gray-300">
                                            {h.peak_rss_kb != null ? `${Math.round(h.peak_rss_kb / 1024)}MB` : "—"}
                                        </td>
                                        <td
                                            className="px-2 py-1 border-b border-gray-800 text-gray-400 font-mono truncate max-w-[120px]"
                                            title={h.worker_image_tag || ""}
                                        >
                                            {h.worker_image_tag || "—"}
                                        </td>
                                        <td
                                            className="px-2 py-1 border-b border-gray-800 text-red-300 truncate max-w-[220px]"
                                            title={h.error || ""}
                                        >
                                            {h.error || ""}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
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
        if (job.duration_ms != null) rows.push(["Elapsed", fmtMs(job.duration_ms)]);
        if (job.peak_rss_kb != null) rows.push(["Peak RSS", fmtBytes(job.peak_rss_kb * 1024)]);
        if (job.cpu_user_ms != null) rows.push(["CPU user", fmtMs(job.cpu_user_ms)]);
        if (job.cpu_sys_ms != null) rows.push(["CPU sys", fmtMs(job.cpu_sys_ms)]);
        if (job.read_bytes != null) rows.push(["Read", fmtBytes(job.read_bytes)]);
        if (job.write_bytes != null) rows.push(["Write", fmtBytes(job.write_bytes)]);
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
    // Runtime shown in the overview: sum-of-cell-times vs active wall clock.
    // Persisted so the operator's choice sticks across visits.
    const [runtimeMode, setRuntimeMode] = useState<RuntimeMode>(
        () => (localStorage.getItem("auditRuntimeMode") === "wall" ? "wall" : "cells"),
    );
    useEffect(() => { localStorage.setItem("auditRuntimeMode", runtimeMode); }, [runtimeMode]);
    // Ambient "audit sweep in progress" toast (shown over the viewer) — operators can hide it here.
    const toastHidden = useAuditToastStore((s) => s.hidden);
    const toggleToast = useAuditToastStore((s) => s.toggle);
    // New-run form: collapsible on mobile (always visible on md+). Auto-collapses
    // when a run is opened so the run detail owns the small screen.
    const [formOpen, setFormOpen] = useState(true);
    const [listError, setListError] = useState<string | null>(null);
    const [detailError, setDetailError] = useState<string | null>(null);
    // Cell whose cross-run history modal is open (from the grid context menu).
    const [historyCell, setHistoryCell] = useState<{key: string; target: string} | null>(null);
    // Cell whose full-detail modal is open (status/metrics/error).
    const [detailsCell, setDetailsCell] = useState<{file: string; target: string} | null>(null);

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
        // Collapse the new-run form on mobile so the selected run's grid gets
        // the viewport (no-op visually on md+, where the form is always shown).
        setFormOpen(false);
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
            {/* Mobile-only collapse header for the new-run form. On md+ the form
                is always shown (this button is hidden), matching desktop where
                screen space isn't scarce. */}
            <button
                type="button"
                onClick={() => setFormOpen((o) => !o)}
                className="md:hidden flex items-center justify-between w-full px-3 py-2 border-b border-gray-800 bg-gray-900/40 text-xs text-gray-200"
                aria-expanded={formOpen}
            >
                <span>New audit run</span>
                <span className="text-gray-400">{formOpen ? "▾ hide" : "▸ show"}</span>
            </button>
            <div className={(formOpen ? "block" : "hidden") + " md:block"}>
                <TriggerForm onCreated={loadRuns}/>
            </div>

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
                                    onClick={() => setRuntimeMode("cells")}
                                    className={"px-2 py-0.5 " + (runtimeMode === "cells"
                                        ? "bg-blue-700 text-white" : "text-gray-300 hover:bg-gray-800")}
                                    title="Sum of every cell's own runtime — the real compute cost, immune to worker parallelism and single-cell re-runs."
                                >
                                    Σ cells
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setRuntimeMode("wall")}
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
                            onClick={toggleToast}
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
                                        <div className="font-mono truncate">
                                            {selectedRun.seq != null && (
                                                <span className="text-gray-500 mr-1">#{selectedRun.seq}</span>
                                            )}
                                            {selectedRun.scope}
                                        </div>
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
                                    {selectedRun.status === "running" ? (
                                        <CancelRunButton
                                            run={selectedRun}
                                            onCancelled={() => {
                                                void loadRuns();
                                                if (selectedId) void loadDetail(selectedId);
                                            }}
                                        />
                                    ) : (
                                        <>
                                            <ValidateRunButton
                                                run={selectedRun}
                                                onValidated={() => {
                                                    void loadRuns();
                                                    if (selectedId) void loadDetail(selectedId);
                                                }}
                                            />
                                            <ReDispatchButton
                                                run={selectedRun}
                                                onDispatched={() => { void loadRuns(); }}
                                            />
                                            <DeleteRunButton
                                                run={selectedRun}
                                                onDeleted={() => {
                                                    setSelectedId(null);
                                                    setSelectedRun(null);
                                                    void loadRuns();
                                                }}
                                            />
                                        </>
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
                            <div className="flex-1 min-h-0 overflow-hidden">
                                <RunGrid
                                    jobs={selectedJobs}
                                    metric={metric}
                                    onCellHistory={(file, target) => setHistoryCell({key: file, target})}
                                    onCellDetails={(file, target) => setDetailsCell({file, target})}
                                    onCellOpen={(file, target) => {
                                        // Load the cell's cached product into the
                                        // underlying scene, from the RUN's scope
                                        // (may differ from the browsed one).
                                        void view_in_3d(file, cellDerivedKey(file, target), selectedRun.scope);
                                    }}
                                    onCellRerun={(file, target) => {
                                        // Re-run just this cell in place (force rebuild). Reopens
                                        // the run; the poller then streams the fresh result in.
                                        void (async () => {
                                            try {
                                                await viewerApi.adminAuditRunRerunCell(selectedRun.id, file, target);
                                                await loadDetail(selectedRun.id);
                                            } catch (e) {
                                                window.alert(`Rerun failed: ${(e as Error).message}`);
                                            }
                                        })();
                                    }}
                                />
                            </div>
                        </>
                    )}
                </div>
            </div>
            {historyCell && (
                <CellHistoryModal cell={historyCell} onClose={() => setHistoryCell(null)}/>
            )}
            {detailsCell && selectedRun && (
                <CellDetailsModal
                    run={selectedRun}
                    file={detailsCell.file}
                    target={detailsCell.target}
                    job={selectedJobs.find(
                        (j) => j.key === detailsCell.file && j.target_format === detailsCell.target,
                    )}
                    onClose={() => setDetailsCell(null)}
                />
            )}
        </div>
    );
};

export default AuditRunsTab;
