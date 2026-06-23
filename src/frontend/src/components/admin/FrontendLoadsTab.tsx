import React, {useCallback, useEffect, useState} from "react";
import {viewerApi} from "@/services/viewerApi";

// Admin "Frontend Loads" tab — aggregates the browser load/render metrics
// the viewer posts (opt-in, Performance options). Two views:
//
//   * Loads  — one row per GLB with the per-phase split (TTFB / download /
//              parse / prepare / GPU) and a dominant-bottleneck label, so a
//              slow load is immediately attributable to IO / network / CPU /
//              GPU. Each row drills into JS Self-Profiling hotspots
//              (TS + WASM self-time) for that file.
//   * Render — one row per GLB with steady-state FPS, CPU vs GPU frame time,
//              draw calls + triangles, and jank, so CPU-bound vs GPU-bound
//              rendering is obvious.

type Cell = Record<string, number | string | null>;

const BOUND_COLORS: Record<string, string> = {
    io: "bg-amber-600",
    network: "bg-sky-600",
    cpu: "bg-rose-600",
    gpu: "bg-violet-600",
    unknown: "bg-gray-600",
};

function ms(v: number | string | null | undefined): string {
    if (v == null || v === "") return "—";
    const n = Number(v);
    return n >= 1000 ? `${(n / 1000).toFixed(2)}s` : `${Math.round(n)}ms`;
}
function bytes(v: number | string | null | undefined): string {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
    return `${n} B`;
}
function num(v: number | string | null | undefined, digits = 0): string {
    if (v == null || v === "") return "—";
    return Number(v).toLocaleString(undefined, {maximumFractionDigits: digits});
}
function shortKey(k: string): string {
    return k.split("/").pop() || k;
}

const WindowPicker: React.FC<{days: number; onChange: (d: number) => void}> = ({days, onChange}) => (
    <label className="text-xs text-gray-300 flex items-center gap-2">
        <span>Window</span>
        <select
            value={days}
            onChange={(e) => onChange(Number(e.target.value))}
            className="bg-gray-700 text-white text-xs rounded-sm px-2 py-1"
        >
            <option value={1}>24h</option>
            <option value={7}>7d</option>
            <option value={30}>30d</option>
            <option value={90}>90d</option>
        </select>
    </label>
);

/** Stacked bar showing the median-phase split per bottleneck class. */
const BoundBar: React.FC<{cell: Cell}> = ({cell}) => {
    const io = Number(cell.io_ms || 0);
    const net = Number(cell.network_ms || 0);
    const cpu = Number(cell.cpu_ms || 0);
    const gpu = Number(cell.gpu_ms || 0);
    const total = io + net + cpu + gpu;
    if (total <= 0) return <span className="text-gray-500">—</span>;
    const seg = (v: number, cls: string, label: string) =>
        v > 0 ? (
            <div
                className={`${cls} h-3`}
                style={{width: `${(v / total) * 100}%`}}
                title={`${label}: ${Math.round(v)}ms (${((v / total) * 100).toFixed(0)}%)`}
            />
        ) : null;
    return (
        <div className="flex w-40 rounded-sm overflow-hidden border border-gray-700">
            {seg(io, BOUND_COLORS.io, "IO / TTFB")}
            {seg(net, BOUND_COLORS.network, "Network")}
            {seg(cpu, BOUND_COLORS.cpu, "CPU")}
            {seg(gpu, BOUND_COLORS.gpu, "GPU")}
        </div>
    );
};

const BoundChip: React.FC<{bound: string}> = ({bound}) => (
    <span className={`${BOUND_COLORS[bound] || BOUND_COLORS.unknown} text-white text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded-sm`}>
        {bound}
    </span>
);

const HotspotsPanel: React.FC<{keyName: string; since: number}> = ({keyName, since}) => {
    const [data, setData] = useState<Awaited<ReturnType<typeof viewerApi.adminFrontendLoadHotspots>> | null>(null);
    const [err, setErr] = useState<string | null>(null);
    useEffect(() => {
        let alive = true;
        viewerApi
            .adminFrontendLoadHotspots({key: keyName, since, limit: 40})
            .then((d) => alive && setData(d))
            .catch((e) => alive && setErr((e as Error).message));
        return () => {
            alive = false;
        };
    }, [keyName, since]);
    if (err) return <div className="text-xs text-red-400 px-3 py-2">hotspots: {err}</div>;
    if (!data) return <div className="text-xs text-gray-400 px-3 py-2">loading hotspots…</div>;
    if (data.loads_in_window === 0)
        return (
            <div className="text-xs text-gray-400 px-3 py-2">
                No profiled loads in window. Enable "Profile calls during load" in Performance options,
                and serve the <code className="text-gray-300">Document-Policy: js-profiling</code> header
                (Chromium only).
            </div>
        );
    return (
        <div className="px-3 py-2 bg-gray-900/60">
            <div className="text-[11px] text-gray-400 mb-1">
                Top self-time frames across {data.loads_in_window} profiled load(s) — TS + WASM
            </div>
            <table className="text-xs w-full">
                <thead className="text-gray-400">
                    <tr>
                        <th className="text-left font-medium">Function</th>
                        <th className="text-right font-medium">Self (sum)</th>
                        <th className="text-right font-medium">Self (avg)</th>
                        <th className="text-right font-medium">Samples</th>
                    </tr>
                </thead>
                <tbody>
                    {data.functions.map((f, i) => (
                        <tr key={i} className="border-t border-gray-800">
                            <td className="py-0.5 font-mono truncate max-w-md" title={f.fn}>
                                {f.is_wasm && <span className="text-violet-300 mr-1">[wasm]</span>}
                                {f.fn}
                            </td>
                            <td className="text-right">{ms(f.self_ms_sum)}</td>
                            <td className="text-right">{ms(f.self_ms_avg)}</td>
                            <td className="text-right">{f.samples}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

const LoadsView: React.FC<{days: number}> = ({days}) => {
    const [cells, setCells] = useState<Cell[]>([]);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setErr(null);
        try {
            const r = await viewerApi.adminFrontendLoads(days);
            setCells(r.cells);
        } catch (e) {
            setErr((e as Error).message || "load failed");
        } finally {
            setLoading(false);
        }
    }, [days]);

    useEffect(() => {
        void load();
    }, [load]);

    if (loading) return <div className="p-4 text-sm text-gray-400">Loading…</div>;
    if (err) return <div className="p-4 text-sm text-red-400">{err}</div>;
    if (cells.length === 0)
        return (
            <div className="p-4 text-sm text-gray-400">
                No model-load metrics in this window. Turn on "Record model-load metrics" in the
                viewer's Performance options (admin), then load a model.
            </div>
        );

    return (
        <table className="text-xs w-full">
            <thead className="text-gray-400 sticky top-0 bg-gray-900">
                <tr className="border-b border-gray-800">
                    <th className="text-left font-medium px-2 py-1">File</th>
                    <th className="text-left font-medium px-2">Bound</th>
                    <th className="text-left font-medium px-2">Phase split (p50)</th>
                    <th className="text-right font-medium px-2">Total p50/p95</th>
                    <th className="text-right font-medium px-2">TTFB</th>
                    <th className="text-right font-medium px-2">Download</th>
                    <th className="text-right font-medium px-2">Parse</th>
                    <th className="text-right font-medium px-2">Prepare</th>
                    <th className="text-right font-medium px-2">GPU</th>
                    <th className="text-right font-medium px-2">Mbps</th>
                    <th className="text-right font-medium px-2">Wire</th>
                    <th className="text-right font-medium px-2">Tris</th>
                    <th className="text-right font-medium px-2">N</th>
                </tr>
            </thead>
            <tbody>
                {cells.map((c) => {
                    const key = String(c.key);
                    const isOpen = expanded === key;
                    return (
                        <React.Fragment key={key}>
                            <tr
                                className="border-b border-gray-800 hover:bg-gray-800/50 cursor-pointer"
                                onClick={() => setExpanded(isOpen ? null : key)}
                            >
                                <td className="px-2 py-1 font-mono truncate max-w-xs" title={key}>
                                    <span className="text-gray-500 mr-1">{isOpen ? "▼" : "▶"}</span>
                                    {shortKey(key)}
                                </td>
                                <td className="px-2"><BoundChip bound={String(c.dominant_bound || "unknown")}/></td>
                                <td className="px-2"><BoundBar cell={c}/></td>
                                <td className="px-2 text-right">{ms(c.total_ms_p50)} / {ms(c.total_ms_p95)}</td>
                                <td className="px-2 text-right">{ms(c.ttfb_ms_p50)}</td>
                                <td className="px-2 text-right">{ms(c.download_ms_p50)}</td>
                                <td className="px-2 text-right">{ms(c.parse_ms_p50)}</td>
                                <td className="px-2 text-right">{ms(c.prepare_ms_p50)}</td>
                                <td className="px-2 text-right">{ms(c.first_render_ms_p50)}</td>
                                <td className="px-2 text-right">{num(c.throughput_mbps_p50, 1)}</td>
                                <td className="px-2 text-right">{bytes(c.transfer_bytes_avg)}</td>
                                <td className="px-2 text-right">{num(c.triangles_p50)}</td>
                                <td className="px-2 text-right">{c.sample_count}</td>
                            </tr>
                            {isOpen && (
                                <tr>
                                    <td colSpan={13}>
                                        <HotspotsPanel keyName={key} since={days}/>
                                    </td>
                                </tr>
                            )}
                        </React.Fragment>
                    );
                })}
            </tbody>
        </table>
    );
};

const RenderView: React.FC<{days: number}> = ({days}) => {
    const [cells, setCells] = useState<Cell[]>([]);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        viewerApi
            .adminRenderProfiles(days)
            .then((r) => alive && setCells(r.cells))
            .catch((e) => alive && setErr((e as Error).message))
            .finally(() => alive && setLoading(false));
        return () => {
            alive = false;
        };
    }, [days]);

    if (loading) return <div className="p-4 text-sm text-gray-400">Loading…</div>;
    if (err) return <div className="p-4 text-sm text-red-400">{err}</div>;
    if (cells.length === 0)
        return (
            <div className="p-4 text-sm text-gray-400">
                No render metrics in this window. Turn on "Record render metrics" in the viewer's
                Performance options (admin), then interact with a model.
            </div>
        );

    return (
        <table className="text-xs w-full">
            <thead className="text-gray-400 sticky top-0 bg-gray-900">
                <tr className="border-b border-gray-800">
                    <th className="text-left font-medium px-2 py-1">File</th>
                    <th className="text-left font-medium px-2">Bound</th>
                    <th className="text-right font-medium px-2">FPS p50</th>
                    <th className="text-right font-medium px-2">FPS min</th>
                    <th className="text-right font-medium px-2">CPU frame p50/p95</th>
                    <th className="text-right font-medium px-2">GPU frame p50/p95</th>
                    <th className="text-right font-medium px-2">Draw calls</th>
                    <th className="text-right font-medium px-2">Tris</th>
                    <th className="text-right font-medium px-2">Programs</th>
                    <th className="text-right font-medium px-2">Long frames</th>
                    <th className="text-right font-medium px-2">Windows</th>
                </tr>
            </thead>
            <tbody>
                {cells.map((c) => (
                    <tr key={String(c.key)} className="border-b border-gray-800 hover:bg-gray-800/50">
                        <td className="px-2 py-1 font-mono truncate max-w-xs" title={String(c.key)}>{shortKey(String(c.key))}</td>
                        <td className="px-2"><BoundChip bound={String(c.dominant_bound || "unknown")}/></td>
                        <td className="px-2 text-right">{num(c.fps_p50, 1)}</td>
                        <td className="px-2 text-right">{num(c.fps_min, 1)}</td>
                        <td className="px-2 text-right">{ms(c.frame_ms_p50)} / {ms(c.frame_ms_p95)}</td>
                        <td className="px-2 text-right">{ms(c.gpu_ms_p50)} / {ms(c.gpu_ms_p95)}</td>
                        <td className="px-2 text-right">{num(c.draw_calls_p50)}</td>
                        <td className="px-2 text-right">{num(c.triangles_p50)}</td>
                        <td className="px-2 text-right">{num(c.programs_max)}</td>
                        <td className="px-2 text-right">{num(c.long_frames_sum)}</td>
                        <td className="px-2 text-right">{c.window_count}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
};

const FrontendLoadsTab: React.FC = () => {
    const [days, setDays] = useState(30);
    const [view, setView] = useState<"loads" | "render">("loads");

    return (
        <div className="flex flex-col h-full overflow-auto">
            <div className="px-3 py-2 border-b border-gray-800 bg-gray-900/40 flex flex-wrap items-center gap-3">
                <div className="flex gap-1 text-sm">
                    <button
                        className={`px-2 py-1 rounded-sm ${view === "loads" ? "bg-gray-700 text-white" : "text-gray-300 hover:bg-gray-800"}`}
                        onClick={() => setView("loads")}
                    >
                        Loads
                    </button>
                    <button
                        className={`px-2 py-1 rounded-sm ${view === "render" ? "bg-gray-700 text-white" : "text-gray-300 hover:bg-gray-800"}`}
                        onClick={() => setView("render")}
                    >
                        Render
                    </button>
                </div>
                <WindowPicker days={days} onChange={setDays}/>
                <div className="text-[11px] text-gray-500 ml-auto">
                    Bottleneck:
                    <span className="ml-2 text-amber-400">IO</span>
                    <span className="ml-2 text-sky-400">network</span>
                    <span className="ml-2 text-rose-400">CPU</span>
                    <span className="ml-2 text-violet-400">GPU</span>
                </div>
            </div>
            <div className="flex-1 min-h-0 overflow-auto">
                {view === "loads" ? <LoadsView days={days}/> : <RenderView days={days}/>}
            </div>
        </div>
    );
};

export default FrontendLoadsTab;
