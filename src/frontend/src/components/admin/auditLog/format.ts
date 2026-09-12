import type {AuditEntry} from "@/services/viewerApi";
import {formatBytes as formatBytesBase, formatMillis} from "@/utils/format";
import {localDateTime} from "@/utils/time";

// Formatting and row predicates shared by the audit log table, its mobile
// cards and the details modal.

// The audit log keeps its own conventions: IEC unit labels, an en dash for a
// missing value, and durations precise enough to compare two runs of one cell.
export const formatDuration = (ms: number | null) => formatMillis(ms, {empty: "–", precise: true});
export const formatBytes = (n: number | null) => formatBytesBase(n, {units: "iec", empty: "–"});

// Whether a row is a candidate for the (i) details modal. Convert
// rows always qualify (they may have metrics, traceback, or both);
// other rows only show the icon when they actually have an error or
// traceback to surface, so the column doesn't fill with no-op
// buttons.
export function hasDetails(e: AuditEntry): boolean {
    if (e.action === "convert") return true;
    // A compile run always has a log to read, even when it succeeded.
    if (e.action === "compile") return true;
    // A plugin job records the same metrics a convert does -- the worker passes
    // them to _audit_done on every outcome -- so a SUCCESSFUL one has something
    // worth opening. Without this it fell through to the error-only rule below
    // and offered no icon at all, leaving those metrics collected but
    // unreachable.
    if (e.action === "plugin_job") return true;
    // Browser view/render rows always carry a client_metrics payload to
    // inspect (per-phase split + per-function frames), fetched lazily.
    if (e.action === "view" || e.action === "render") return true;
    return Boolean(e.error || e.traceback);
}

export function isClientMetricsRow(e: AuditEntry): boolean {
    return e.action === "view" || e.action === "render";
}

export function hasMetrics(e: AuditEntry): boolean {
    return (
        e.cpu_user_ms != null ||
        e.cpu_sys_ms != null ||
        e.peak_rss_kb != null ||
        e.read_bytes != null ||
        e.write_bytes != null ||
        e.profile_key != null ||
        e.duration_ms != null
    );
}

export function statusClass(s: string | null): string {
    if (s === "ok" || s === "done") return "text-green-400";
    if (s === "error") return "text-red-400";
    if (s === "queued") return "text-yellow-300";
    return "text-gray-300";
}

// In-browser (WASM) conversions get a ``wasm-`` job id and a ``wasm:``
// image tag from the audit/local endpoints; surface either as a badge so
// operators can tell client-side rows from worker rows at a glance.
export function isWasmEntry(e: {job_id?: string | null; worker_image_tag?: string | null}): boolean {
    return (e.job_id || "").startsWith("wasm-") || (e.worker_image_tag || "").startsWith("wasm:");
}

export function shortSub(s: string | null): string {
    if (!s) return "";
    if (s.length <= 12) return s;
    return `${s.slice(0, 8)}…${s.slice(-4)}`;
}

// Hover tooltip for the user column: display name + email (resolved server-side from the users
// table), falling back to the raw subject when a name/email isn't on file.
export function userTooltip(e: AuditEntry): string {
    const lines: string[] = [];
    if (e.user_display_name) lines.push(e.user_display_name);
    if (e.user_email) lines.push(e.user_email);
    if (e.user_sub) lines.push(e.user_sub);
    return lines.join("\n");
}

export function formatTs(ts: string | null): string {
    // Local time, not the raw UTC string: an earlier version sliced the wire
    // value verbatim and the table read hours behind for any viewer east of UTC.
    return localDateTime(ts);
}

export function shortFile(p: string): string {
    if (!p) return "";
    const parts = p.split("/");
    if (parts.length <= 2) return p;
    return ".../" + parts.slice(-2).join("/");
}

export function formatPercall(v: number): string {
    if (!isFinite(v) || v === 0) return "0";
    if (v >= 1) return v.toFixed(3);
    if (v >= 1e-3) return (v * 1000).toFixed(2) + "ms";
    return (v * 1e6).toFixed(1) + "µs";
}
