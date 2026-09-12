import React, {useCallback, useEffect, useState} from "react";
import {viewerApi} from "@/services/viewerApi";
import {DataTable, DataTableColumn} from "@/components/common/DataTable";

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

const HotspotsPanel: React.FC<{keyName: string; since: number; kind?: "view" | "render"}> = ({keyName, since, kind = "view"}) => {
    const [data, setData] = useState<Awaited<ReturnType<typeof viewerApi.adminFrontendLoadHotspots>> | null>(null);
    const [err, setErr] = useState<string | null>(null);
    useEffect(() => {
        let alive = true;
        viewerApi
            .adminFrontendLoadHotspots({key: keyName, since, limit: 40, kind})
            .then((d) => alive && setData(d))
            .catch((e) => alive && setErr((e as Error).message));
        return () => {
            alive = false;
        };
    }, [keyName, since, kind]);
    if (err) return <div className="text-xs text-red-400 px-3 py-2">hotspots: {err}</div>;
    if (!data) return <div className="text-xs text-gray-400 px-3 py-2">loading hotspots…</div>;
    if (data.loads_in_window === 0)
        return (
            <div className="text-xs text-gray-400 px-3 py-2">
                No profiled {kind === "render" ? "render windows" : "loads"} in window. Enable "Profile calls"
                in Performance options, and serve the{" "}
                <code className="text-gray-300">Document-Policy: js-profiling</code> header (Chromium only).
                {kind === "render" && " These are main-thread (CPU) frames; GPU-bound cost shows in the GPU column, not here."}
            </div>
        );
    return (
        <div className="px-3 py-2 bg-gray-900/60">
            <div className="text-[11px] text-gray-400 mb-1">
                Top self-time frames across {data.loads_in_window} profiled {kind === "render" ? "render window(s)" : "load(s)"} — TS + WASM
                {kind === "render" && " · main-thread only (GPU-bound shows in gpu_ms)"}
            </div>
            <DataTable
                wrap={false}
                columns={HOTSPOT_FN_COLUMNS}
                rows={data.functions}
                rowKey={(_f, i) => i}
                className="text-xs w-full"
                theadClassName="text-gray-400"
                rowClassName="border-t border-gray-800"
            />
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
        <DataTable
            wrap={false}
            columns={loadColumns(expanded)}
            rows={cells}
            rowKey={(c) => String(c.key)}
            className="text-xs w-full"
            stickyHeader
            theadClassName="text-gray-400 bg-gray-900"
            headerRowClassName="border-b border-gray-800"
            cellClassName="px-2 text-right"
            rowClassName="border-b border-gray-800 hover:bg-gray-800/50 cursor-pointer"
            rowProps={(c) => ({onClick: () => setExpanded(expanded === String(c.key) ? null : String(c.key))})}
            renderAfterRow={(c) => expanded === String(c.key) && (
                <tr>
                    <td colSpan={13}>
                        <HotspotsPanel keyName={String(c.key)} since={days}/>
                    </td>
                </tr>
            )}
        />
    );
};

const RenderView: React.FC<{days: number}> = ({days}) => {
    const [cells, setCells] = useState<Cell[]>([]);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);

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
        <DataTable
            wrap={false}
            columns={renderColumns(expanded)}
            rows={cells}
            rowKey={(c) => String(c.key)}
            className="text-xs w-full"
            stickyHeader
            theadClassName="text-gray-400 bg-gray-900"
            headerRowClassName="border-b border-gray-800"
            cellClassName="px-2 text-right"
            rowClassName="border-b border-gray-800 hover:bg-gray-800/50 cursor-pointer"
            rowProps={(c) => ({onClick: () => setExpanded(expanded === String(c.key) ? null : String(c.key))})}
            renderAfterRow={(c) => expanded === String(c.key) && (
                <tr>
                    <td colSpan={11}>
                        <HotspotsPanel keyName={String(c.key)} since={days} kind="render"/>
                    </td>
                </tr>
            )}
        />
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


type HotspotFn = Awaited<ReturnType<typeof viewerApi.adminFrontendLoadHotspots>>["functions"][number];

const HOTSPOT_FN_COLUMNS: DataTableColumn<HotspotFn>[] = [
    {
        key: "fn",
        header: "Function",
        headerClassName: "text-left font-medium",
        cellClassName: "py-0.5 font-mono truncate max-w-md",
        title: (f) => f.fn,
        cell: (f) => (
            <>
                {f.is_wasm && <span className="text-violet-300 mr-1">[wasm]</span>}
                {f.fn}
            </>
        ),
    },
    {key: "self_sum", header: "Self (sum)", headerClassName: "text-right font-medium", cellClassName: "text-right", cell: (f) => ms(f.self_ms_sum)},
    {key: "self_avg", header: "Self (avg)", headerClassName: "text-right font-medium", cellClassName: "text-right", cell: (f) => ms(f.self_ms_avg)},
    {key: "samples", header: "Samples", headerClassName: "text-right font-medium", cellClassName: "text-right", cell: (f) => f.samples},
];

const CELL_TH_LEFT = "text-left font-medium px-2";
const CELL_TH_RIGHT = "text-right font-medium px-2";

// First column of both per-file tables: the expand chevron + shortened key.
function fileColumn(expanded: string | null): DataTableColumn<Cell> {
    return {
        key: "file",
        header: "File",
        headerClassName: "text-left font-medium px-2 py-1",
        cellClassName: "px-2 py-1 font-mono truncate max-w-xs",
        title: (c) => String(c.key),
        cell: (c) => {
            const key = String(c.key);
            return (
                <>
                    <span className="text-gray-500 mr-1">{expanded === key ? "▼" : "▶"}</span>
                    {shortKey(key)}
                </>
            );
        },
    };
}

const boundColumn: DataTableColumn<Cell> = {
    key: "bound",
    header: "Bound",
    headerClassName: CELL_TH_LEFT,
    cellClassName: "px-2",
    cell: (c) => <BoundChip bound={String(c.dominant_bound || "unknown")}/>,
};

function loadColumns(expanded: string | null): DataTableColumn<Cell>[] {
    return [
        fileColumn(expanded),
        boundColumn,
        {key: "split", header: "Phase split (p50)", headerClassName: CELL_TH_LEFT, cellClassName: "px-2", cell: (c) => <BoundBar cell={c}/>},
        {key: "total", header: "Total p50/p95", headerClassName: CELL_TH_RIGHT, cell: (c) => <>{ms(c.total_ms_p50)} / {ms(c.total_ms_p95)}</>},
        {key: "ttfb", header: "TTFB", headerClassName: CELL_TH_RIGHT, cell: (c) => ms(c.ttfb_ms_p50)},
        {key: "download", header: "Download", headerClassName: CELL_TH_RIGHT, cell: (c) => ms(c.download_ms_p50)},
        {key: "parse", header: "Parse", headerClassName: CELL_TH_RIGHT, cell: (c) => ms(c.parse_ms_p50)},
        {key: "prepare", header: "Prepare", headerClassName: CELL_TH_RIGHT, cell: (c) => ms(c.prepare_ms_p50)},
        {key: "gpu", header: "GPU", headerClassName: CELL_TH_RIGHT, cell: (c) => ms(c.first_render_ms_p50)},
        {key: "mbps", header: "Mbps", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.throughput_mbps_p50, 1)},
        {key: "wire", header: "Wire", headerClassName: CELL_TH_RIGHT, cell: (c) => bytes(c.transfer_bytes_avg)},
        {key: "tris", header: "Tris", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.triangles_p50)},
        {key: "n", header: "N", headerClassName: CELL_TH_RIGHT, cell: (c) => c.sample_count},
    ];
}

function renderColumns(expanded: string | null): DataTableColumn<Cell>[] {
    return [
        fileColumn(expanded),
        boundColumn,
        {key: "fps_p50", header: "FPS p50", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.fps_p50, 1)},
        {key: "fps_min", header: "FPS min", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.fps_min, 1)},
        {key: "cpu_frame", header: "CPU frame p50/p95", headerClassName: CELL_TH_RIGHT, cell: (c) => <>{ms(c.frame_ms_p50)} / {ms(c.frame_ms_p95)}</>},
        {key: "gpu_frame", header: "GPU frame p50/p95", headerClassName: CELL_TH_RIGHT, cell: (c) => <>{ms(c.gpu_ms_p50)} / {ms(c.gpu_ms_p95)}</>},
        {key: "draw_calls", header: "Draw calls", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.draw_calls_p50)},
        {key: "tris", header: "Tris", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.triangles_p50)},
        {key: "programs", header: "Programs", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.programs_max)},
        {key: "long_frames", header: "Long frames", headerClassName: CELL_TH_RIGHT, cell: (c) => num(c.long_frames_sum)},
        {key: "windows", header: "Windows", headerClassName: CELL_TH_RIGHT, cell: (c) => c.window_count},
    ];
}

export default FrontendLoadsTab;
