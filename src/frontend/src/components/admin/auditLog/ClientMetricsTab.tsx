import React, {useEffect, useState} from "react";
import {AuditEntry, viewerApi} from "@/services/viewerApi";
import {formatBytes, formatDuration} from "./format";

// Per-row inspection of a browser view/render event: the full
// client_metrics payload (lazily fetched), shown as a phase/scalar grid
// plus the per-function self-time frames when call profiling was on.
const CM_MS_KEYS = new Set([
    "total_ms", "ttfb_ms", "download_ms", "decompress_ms", "parse_ms", "prepare_ms",
    "first_render_ms", "dns_ms", "tcp_ms", "tls_ms", "long_task_ms", "blocking_ms",
    "window_ms", "frame_ms_p50", "frame_ms_p95", "gpu_ms_p50", "gpu_ms_p95",
]);
const CM_BYTE_KEYS = new Set(["transfer_bytes", "decoded_bytes"]);

const ClientMetricsTab: React.FC<{entry: AuditEntry}> = ({entry}) => {
    const [cm, setCm] = useState<Record<string, unknown> | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        viewerApi
            .adminAuditClientMetrics(entry.id)
            .then((d) => alive && setCm(d.client_metrics))
            .catch((e) => alive && setErr((e as Error).message))
            .finally(() => alive && setLoading(false));
        return () => {
            alive = false;
        };
    }, [entry.id]);

    if (loading) return <div className="px-4 py-3 text-xs text-gray-400">Loading…</div>;
    if (err) return <div className="px-4 py-3 text-xs text-red-300">{err}</div>;
    if (!cm) return <div className="px-4 py-3 text-xs text-gray-400">No client metrics recorded for this entry.</div>;

    const frames = Array.isArray(cm.profile_frames)
        ? (cm.profile_frames as Array<{fn?: string; self_ms?: number; total_ms?: number}>)
        : [];
    // Scalar fields → a sorted key/value grid, with ms/bytes formatting.
    const scalars = Object.entries(cm)
        .filter(([k, v]) => k !== "profile_frames" && (typeof v === "number" || typeof v === "string" || typeof v === "boolean"))
        .sort(([a], [b]) => a.localeCompare(b));
    // Performance toggles active when this load/render was captured.
    const perfOptions =
        cm.perf_options && typeof cm.perf_options === "object" && !Array.isArray(cm.perf_options)
            ? (cm.perf_options as Record<string, unknown>)
            : null;
    const fmt = (k: string, v: unknown): string => {
        if (typeof v === "number") {
            if (CM_MS_KEYS.has(k)) return formatDuration(v);
            if (CM_BYTE_KEYS.has(k)) return formatBytes(v);
            return String(Math.round(v * 100) / 100);
        }
        return String(v);
    };

    return (
        <div className="px-4 py-3 text-xs text-gray-200 space-y-3">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 font-mono">
                {scalars.map(([k, v]) => (
                    <React.Fragment key={k}>
                        <dt className="text-gray-400">{k}</dt>
                        <dd className="text-gray-100">{fmt(k, v)}</dd>
                    </React.Fragment>
                ))}
            </dl>
            {perfOptions ? (
                <div className="pt-2 border-t border-gray-800">
                    <div className="text-[11px] text-gray-400 mb-1">Performance options (active at capture)</div>
                    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 font-mono">
                        {Object.entries(perfOptions)
                            .sort(([a], [b]) => a.localeCompare(b))
                            .map(([k, v]) => (
                                <React.Fragment key={k}>
                                    <dt className="text-gray-400">{k}</dt>
                                    <dd className="text-gray-100">{String(v)}</dd>
                                </React.Fragment>
                            ))}
                    </dl>
                </div>
            ) : null}
            {frames.length > 0 ? (
                <div className="pt-2 border-t border-gray-800">
                    <div className="text-[11px] text-gray-400 mb-1">
                        Call hotspots (self-time, TS + WASM)
                        {entry.action === "render" && " · main-thread only (GPU-bound shows in gpu_ms)"}
                    </div>
                    <table className="w-full">
                        <thead className="text-gray-400">
                            <tr>
                                <th className="text-left font-medium">Function</th>
                                <th className="text-right font-medium">Self</th>
                                <th className="text-right font-medium">Total</th>
                            </tr>
                        </thead>
                        <tbody>
                            {frames.map((f, i) => (
                                <tr key={i} className="border-t border-gray-800">
                                    <td className="py-0.5 font-mono truncate max-w-md" title={f.fn}>
                                        {typeof f.fn === "string" && f.fn.toLowerCase().includes("wasm") && (
                                            <span className="text-violet-300 mr-1">[wasm]</span>
                                        )}
                                        {f.fn}
                                    </td>
                                    <td className="text-right font-mono">{formatDuration(f.self_ms ?? null)}</td>
                                    <td className="text-right font-mono">{formatDuration(f.total_ms ?? null)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="text-[10px] text-gray-500 pt-2 border-t border-gray-800">
                    No call profile on this event. Enable "Profile calls" in Performance options
                    (Chromium + Document-Policy: js-profiling).
                </div>
            )}
        </div>
    );
};

export default ClientMetricsTab;
