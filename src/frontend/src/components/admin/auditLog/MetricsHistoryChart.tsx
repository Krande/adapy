import React, {useEffect, useState} from "react";
import {ApiError, MetricsSample, viewerApi} from "@/services/viewerApi";
import {formatBytes} from "./format";

const MetricsHistoryChart: React.FC<{auditId: number; cores?: number; native?: boolean}> = ({
    auditId,
    cores,
    native,
}) => {
    const [samples, setSamples] = useState<MetricsSample[] | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setErr(null);
        viewerApi.adminMetricsHistory(auditId)
            .then((r) => {
                if (!cancelled) setSamples(r.samples);
            })
            .catch((e) => {
                if (!cancelled) setErr(e instanceof ApiError ? e.detail || e.message : String(e));
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [auditId]);

    if (loading) {
        return <div className="text-[10px] text-gray-400">Loading resource timeline…</div>;
    }
    if (err) {
        return <div className="text-[10px] text-red-300 break-all">timeline: {err}</div>;
    }
    if (!samples || samples.length === 0) {
        return (
            <div className="text-[10px] text-gray-500 pt-1 border-t border-gray-800">
                No per-heartbeat samples for this run. Older jobs predate the
                subprocess wrapper, or the worker died before any sample landed.
            </div>
        );
    }

    const maxElapsed = Math.max(...samples.map((s) => s.elapsed_s), 1);
    const maxRss = Math.max(...samples.map((s) => Math.max(s.rss_kb, s.peak_rss_kb)), 1);
    const maxIo = Math.max(...samples.map((s) => Math.max(s.read_bytes, s.write_bytes)), 1);

    // cpu_user_ms/cpu_sys_ms are CUMULATIVE, so plotting them is just a monotonic ramp. Instead show
    // utilization: Δ(user+sys) / Δwall between consecutive samples = CPU cores busy in that window.
    // When the pod's core count is known (convert_meta.cpu_cores) render it as % of all cores (0–100%);
    // otherwise as absolute cores busy.
    const cpuCoresBusy: number[] = samples.map((s, i) => {
        const prevCpu = i === 0 ? 0 : samples[i - 1].cpu_user_ms + samples[i - 1].cpu_sys_ms;
        const prevT = i === 0 ? 0 : samples[i - 1].elapsed_s;
        const dCpuMs = s.cpu_user_ms + s.cpu_sys_ms - prevCpu;
        const dWallMs = (s.elapsed_s - prevT) * 1000;
        return dWallMs > 0 ? Math.max(0, dCpuMs / dWallMs) : 0;
    });
    const maxCoresBusy = Math.max(...cpuCoresBusy, 0.01);
    const cpuFullScale = cores && cores > 0 ? cores : Math.max(Math.ceil(maxCoresBusy), 1);
    const cpuLabel =
        cores && cores > 0
            ? `${Math.round((maxCoresBusy / cores) * 100)}% of ${cores} cores`
            : `${maxCoresBusy.toFixed(1)} cores`;

    // Per-thread (≈ per-core) utilization envelope — only for the in-process native engine, where the
    // C++ tessellation threads live in the sampled process. Match threads by tid across consecutive
    // samples, take each thread's Δcpu/Δwall (1.0 = one core's worth), then SORT descending per sample
    // so each plotted line is a "core rank": N lines pinned high = N cores saturated, vs all N at
    // mid-height = N cores half-busy — the distinction the aggregate % can't make.
    const hasPerThread =
        !!native && samples.some((s) => s.per_thread_cpu_ms && Object.keys(s.per_thread_cpu_ms).length > 0);
    const nLanes = hasPerThread ? Math.max(cores && cores > 0 ? cores : 1, 1) : 0;
    const lanes: number[][] = hasPerThread
        ? samples.map((s, i) => {
              const cur = s.per_thread_cpu_ms || {};
              const prev = i > 0 ? samples[i - 1].per_thread_cpu_ms || {} : {};
              const dWallMs = (s.elapsed_s - (i > 0 ? samples[i - 1].elapsed_s : 0)) * 1000;
              const busy = Object.keys(cur)
                  .map((tid) => (dWallMs > 0 ? Math.max(0, (cur[tid] - (prev[tid] ?? 0)) / dWallMs) : 0))
                  .sort((a, b) => b - a);
              return Array.from({length: nLanes}, (_, k) => busy[k] ?? 0); // rank-ordered, padded to nLanes
          })
        : [];
    const laneColors = Array.from({length: nLanes}, (_, k) => `hsl(152 ${72 - k * 6}% ${62 - k * 5}%)`);
    const peakLanes = hasPerThread
        ? Math.max(0, ...samples.map((_, i) => lanes[i].filter((v) => v >= 0.5).length))
        : 0;

    return (
        <div className="space-y-2 pt-2 border-t border-gray-800">
            <div className="text-[10px] uppercase tracking-wide text-gray-500">
                Resource timeline ({samples.length} samples · {maxElapsed.toFixed(0)}s)
            </div>
            <ChartPanel
                title="RSS"
                yLabel={formatBytes(maxRss * 1024)}
                series={[
                    {color: "#60a5fa", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.rss_kb / maxRss]), label: "rss"},
                    {color: "#f87171", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.peak_rss_kb / maxRss]), label: "peak"},
                ]}
            />
            {hasPerThread ? (
                <CpuPerCorePanel
                    samples={samples}
                    lanes={lanes}
                    nLanes={nLanes}
                    cores={cores && cores > 0 ? cores : nLanes}
                    maxElapsed={maxElapsed}
                    colors={laneColors}
                    peakLanes={peakLanes}
                />
            ) : (
                <ChartPanel
                    title={cores ? "CPU util (% of cores)" : "CPU (cores busy)"}
                    yLabel={cpuLabel}
                    series={[
                        {
                            color: "#34d399",
                            points: samples.map((s, i) => [s.elapsed_s / maxElapsed, Math.min(1, cpuCoresBusy[i] / cpuFullScale)]),
                            label: "cpu",
                        },
                    ]}
                />
            )}
            <ChartPanel
                title="IO bytes"
                yLabel={formatBytes(maxIo)}
                series={[
                    {color: "#a78bfa", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.read_bytes / maxIo]), label: "read"},
                    {color: "#fbbf24", points: samples.map((s) => [s.elapsed_s / maxElapsed, s.write_bytes / maxIo]), label: "write"},
                ]}
            />
        </div>
    );
};

// Stacked per-core CPU utilization (native engine). Each band is one core, rank-sorted busiest →
// least and stacked from the bottom; the y-axis ceiling is the pod's core count. So the FILLED
// HEIGHT at any instant = "cores busy," and saturation reads directly off the chart: the stack
// hugging the top across the whole width = all cores saturated for the whole conversion; a dip = an
// idle stretch. 3 full bands reaching 3/4 (native reserves one core for the parent) is unmistakably
// different from 4 half-height bands reaching 2/4 — which the aggregate % and overlaid lines blur.
const CpuPerCorePanel: React.FC<{
    samples: MetricsSample[];
    lanes: number[][];
    nLanes: number;
    cores: number;
    maxElapsed: number;
    colors: string[];
    peakLanes: number;
}> = ({samples, lanes, nLanes, cores, maxElapsed, colors, peakLanes}) => {
    const W = 320;
    const H = 56;
    const x = (i: number) => (samples[i].elapsed_s / maxElapsed) * W;
    const yAt = (cumCores: number) => H - Math.min(1, cumCores / cores) * H;
    const cum = (i: number, k: number) => {
        let c = 0;
        for (let j = 0; j < k; j++) c += lanes[i][j] ?? 0;
        return c;
    };
    return (
        <div>
            <div className="flex items-baseline justify-between gap-2 mb-0.5">
                <div className="text-[10px] text-gray-400 font-mono">CPU per core (native)</div>
                <div className="text-[10px] text-gray-500 font-mono">
                    top = {cores} cores · peak {peakLanes} saturated
                </div>
            </div>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14 bg-gray-900/60 border border-gray-800 rounded-sm">
                {Array.from({length: nLanes}, (_, k) => {
                    const top = samples.map(
                        (_, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${yAt(cum(i, k + 1)).toFixed(1)}`,
                    );
                    const bot: string[] = [];
                    for (let i = samples.length - 1; i >= 0; i--) {
                        bot.push(`L${x(i).toFixed(1)},${yAt(cum(i, k)).toFixed(1)}`);
                    }
                    return (
                        <path key={k} d={`${top.join(" ")} ${bot.join(" ")} Z`} fill={colors[k]} fillOpacity={0.85} stroke="none"/>
                    );
                })}
            </svg>
        </div>
    );
};

const ChartPanel: React.FC<{
    title: string;
    yLabel: string;
    series: {color: string; points: [number, number][]; label: string}[];
}> = ({title, yLabel, series}) => {
    const W = 320;
    const H = 56;
    return (
        <div>
            <div className="flex items-baseline justify-between gap-2 mb-0.5">
                <div className="text-[10px] text-gray-400 font-mono">{title}</div>
                <div className="text-[10px] text-gray-500 font-mono">max ≈ {yLabel}</div>
            </div>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14 bg-gray-900/60 border border-gray-800 rounded-sm">
                {series.map((s, i) => {
                    if (s.points.length === 0) return null;
                    const d = s.points
                        .map(([x, y], idx) =>
                            `${idx === 0 ? "M" : "L"}${(x * W).toFixed(2)},${(H - y * H).toFixed(2)}`,
                        )
                        .join(" ");
                    return (
                        <g key={i}>
                            <path d={d} stroke={s.color} strokeWidth={1.2} fill="none"/>
                        </g>
                    );
                })}
            </svg>
            <div className="flex gap-3 mt-0.5 text-[10px] text-gray-500 font-mono">
                {series.map((s, i) => (
                    <span key={i} style={{color: s.color}}>● <span className="text-gray-400">{s.label}</span></span>
                ))}
            </div>
        </div>
    );
};

export default MetricsHistoryChart;
