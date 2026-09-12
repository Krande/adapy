import type {AuditRun, AuditRunJob} from "@/services/viewerApi";
import {runtime} from "@/runtime/config";
import {formatBytes, formatMillis} from "@/utils/format";

// Pure helpers for the per-run file × target grid: metric selection, cell
// values/labels/tooltips, colour buckets and per-source quality flags.

export const POLL_INTERVAL_MS = 5000;

export type MetricKey = "status" | "validation" | "peak_rss_kb" | "duration_ms" | "mem_per_mb" | "write_bytes";

export const METRIC_LABELS: Record<MetricKey, string> = {
    status: "Pass / fail",
    validation: "Validation",
    peak_rss_kb: "Peak RSS",
    duration_ms: "Elapsed",
    mem_per_mb: "RSS / source MB",
    write_bytes: "Output size",
};

export const STATUS_COLOR: Record<string, string> = {
    done: "bg-emerald-900/60 border-emerald-600 text-emerald-100",
    ok: "bg-emerald-900/60 border-emerald-600 text-emerald-100",
    error: "bg-red-900/60 border-red-600 text-red-100",
    failed: "bg-red-900/60 border-red-600 text-red-100",
    queued: "bg-amber-900/40 border-amber-600 text-amber-100",
    running: "bg-blue-900/40 border-blue-600 text-blue-100",
    cancelled: "bg-gray-800 border-gray-600 text-gray-300",
    skipped: "bg-gray-800 border-gray-600 text-gray-400",
};

export type RuntimeMode = "cells" | "wall";

// Two equally-relevant views of a run's runtime, switched by the overview toggle:
//   "cells" — SUM of every cell's own duration_ms: the real compute cost. Immune
//             to worker parallelism and to a single-cell re-run reopening the run
//             (which would inflate wall clock with the idle gap since the first
//             run). Recomputes server-side whenever a cell's row changes.
//   "wall"  — active wall clock (finished−started−idle): how long the operator
//             actually waited, which parallelism compresses below the cell sum.
// "cells" falls back to wall clock for older runs / in-flight rows with no sum yet.
export function fmtRunDuration(run: AuditRun, mode: RuntimeMode): string {
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
export function buildGrid(jobs: AuditRunJob[]): {
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
export function cellValue(metric: MetricKey, job: AuditRunJob | undefined): number | null {
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

export function cellLabel(metric: MetricKey, job: AuditRunJob | undefined): string {
    if (!job) return "";
    if (metric === "status" || metric === "validation") return job.status ?? "";
    const v = cellValue(metric, job);
    if (v == null) return "—";
    if (metric === "peak_rss_kb") return formatBytes(v * 1024);
    if (metric === "duration_ms") return formatMillis(v);
    if (metric === "write_bytes") return formatBytes(v);
    if (metric === "mem_per_mb") return `${v.toFixed(1)}×`;
    return "";
}

export function cellTooltip(job: AuditRunJob | undefined): string {
    if (!job) return "no job";
    const parts: string[] = [];
    if (job.status) parts.push(`status: ${job.status}`);
    if (job.duration_ms != null) parts.push(`elapsed: ${formatMillis(job.duration_ms)}`);
    if (job.peak_rss_kb != null) parts.push(`peak rss: ${formatBytes(job.peak_rss_kb * 1024)}`);
    if (job.read_bytes != null) parts.push(`read: ${formatBytes(job.read_bytes)}`);
    if (job.write_bytes != null) parts.push(`write: ${formatBytes(job.write_bytes)}`);
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
export const METRIC_COLOR_BUCKETS: {cls: string}[] = [
    {cls: "bg-emerald-900/40 border-emerald-700 text-emerald-100"},  // fast / light
    {cls: "bg-amber-900/40 border-amber-700 text-amber-100"},        // medium
    {cls: "bg-orange-900/60 border-orange-600 text-orange-100"},     // heavy
    {cls: "bg-red-900/60 border-red-600 text-red-100"},              // outlier
];

// The derived-blob key a cell produced. Mirrors the server's
// derived_key_for convention: _derived/<source>.<target>, except a glb
// target of a source that is already a .glb has no derivation.
export function cellDerivedKey(file: string, target: string): string {
    if (target === "glb" && file.toLowerCase().endsWith(".glb")) return file;
    return `_derived/${file}.${target}`;
}

// Whether a cell's product can be opened in the 3D viewer. The viewer mounts
// GLB directly and converts any target the converter can turn into GLB
// (ifc/step/xml/...); parity has no artifact, and a non-done cell has nothing
// cached to open. Mirrors ConversionRow's canPreview gate.
export function cellViewable(job: AuditRunJob | undefined): boolean {
    if (!job || !job.key || !job.target_format) return false;
    if (!(job.status === "done" || job.status === "ok")) return false;
    const t = job.target_format;
    if (t === "glb") return true;
    if (t === "parity") return false;
    return runtime.conversionTargetsFor(t).includes("glb");
}

export type CellFlag = {key: string; label: string; title: string; cls: string};

// Per-source quality flags, aggregated across the row's cells' convert_meta. Mirrors the
// PerformanceTab "streaming" pill idea: a compact badge per detected issue.
//   occ_fallback — the NGEOM/libtess2 (OCC-free) tessellation silently fell back to OCC.
//   distorted    — a converted mesh has heavily distorted (degenerate/sliver) triangles.
export function sourceFlags(cells: Map<string, AuditRunJob>, targets: string[], file: string): CellFlag[] {
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
