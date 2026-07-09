import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {useMeshPanelStore} from "@/state/meshPanelStore";
import {useOptionsStore} from "@/state/optionsStore";
import CollapsibleSection from "@/components/common/CollapsibleSection";
import {collectGeomEntries, focusGeomEntry, endGeomWalk, type GeomEntry} from "@/utils/scene/galleryWalk";
import {queryNameFromRangeId} from "@/utils/mesh_select/queryMeshDrawRange";

// "Mesh" mode of the Scene panel (Scene dropdown → Mesh). Scans every geom in the scene for
// "crows-nest" tessellation spikes — the same detector the gallery "distorted" walk uses
// (utils/mesh_select/meshStats) — and lists the offenders in a distortion-sorted table. The two
// spike thresholds are editable; "Rescan" re-runs at the current thresholds. Clicking a row selects
// + frames that geom (triangle edges on) so the spike is visible. Rendered inside SceneInfoBox, which
// supplies the panel chrome + the mode dropdown — so this is a bare section, no outer chrome.

interface Row {
    mesh: GeomEntry["mesh"];
    rangeId: string;
    triangles: number;
    spike: number;
    spikeTris: number;
    name: string;
}

type SortKey = "spike" | "spikeTris" | "triangles" | "name";

const num = (n: number, digits = 1) => (Number.isFinite(n) ? n.toFixed(digits) : "—");

const MeshDistortionSection: React.FC = () => {
    const {spikeAspectMin, spikeOutlierK, setSpikeAspectMin, setSpikeOutlierK, resetThresholds} =
        useMeshPanelStore();
    const {showEdges, setShowEdges, hideTessellationEdges, setHideTessellationEdges} = useOptionsStore();

    const [rows, setRows] = useState<Row[]>([]);
    const [scanning, setScanning] = useState(false);
    const [scanned, setScanned] = useState(false);
    const [isolate, setIsolate] = useState(false);
    const [sortKey, setSortKey] = useState<SortKey>("spike");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [selectedRange, setSelectedRange] = useState<string | null>(null);
    const scanSeq = useRef(0);

    const rescan = useCallback(async () => {
        const seq = ++scanSeq.current;
        setScanning(true);
        const entries = collectGeomEntries("distorted", {spikeAspectMin, spikeOutlierK});
        if (scanSeq.current !== seq) return;
        const base: Row[] = entries.map((e) => ({
            mesh: e.mesh,
            rangeId: e.rangeId,
            triangles: e.triangles,
            spike: e.spike,
            spikeTris: e.spikeTris,
            name: e.rangeId,
        }));
        setRows(base);
        setScanning(false);
        setScanned(true);
        const names = await Promise.all(
            base.map((r) => queryNameFromRangeId(r.mesh.unique_key, r.rangeId).catch(() => null)),
        );
        if (scanSeq.current !== seq) return;
        setRows(base.map((r, i) => ({...r, name: names[i] || r.rangeId})));
    }, [spikeAspectMin, spikeOutlierK]);

    // Scan once when the Mesh mode is opened. Threshold edits don't auto-rescan (avoid a heavy
    // full-scene scan on every keystroke) — the user hits Rescan.
    useEffect(() => {
        void rescan();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const sorted = useMemo(() => {
        const dir = sortDir === "desc" ? -1 : 1;
        return [...rows].sort((a, b) => {
            if (sortKey === "name") return dir * a.name.localeCompare(b.name);
            return dir * (a[sortKey] - b[sortKey]);
        });
    }, [rows, sortKey, sortDir]);

    const onSort = (key: SortKey) => {
        if (key === sortKey) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
        else {
            setSortKey(key);
            setSortDir(key === "name" ? "asc" : "desc");
        }
    };

    const onRowClick = (r: Row) => {
        setSelectedRange(r.rangeId);
        void focusGeomEntry(
            {mesh: r.mesh, rangeId: r.rangeId, triangles: r.triangles, density: 0, spike: r.spike, spikeTris: r.spikeTris},
            {hideUnselected: isolate, forceEdges: true},
        );
    };

    const sortArrow = (key: SortKey) => (sortKey === key ? (sortDir === "desc" ? " ▼" : " ▲") : "");
    const thClass = "sticky top-0 bg-gray-800 px-2 py-1 text-left font-semibold cursor-pointer select-none whitespace-nowrap";

    return (
        <div className="space-y-1">
            <CollapsibleSection title="Options" defaultOpen>
                <div className="space-y-2 pt-1">
                    {/* Mesh triangle visibility — see the triangulation to judge tessellation/welding.
                        Edge overlay toggles live; the full triangulation grid is baked at load (reload). */}
                    <div className="flex items-center gap-4 text-sm">
                        <label className="flex items-center space-x-1">
                            <input type="checkbox" className="no-drag" checked={showEdges} onChange={() => setShowEdges(!showEdges)}/>
                            <span>Mesh edges</span>
                        </label>
                        <label
                            className="flex items-center space-x-1"
                            title="Show every triangle edge (the tessellation grid), not just feature edges. Baked at load — reload the model to apply."
                        >
                            <input
                                type="checkbox"
                                className="no-drag"
                                checked={!hideTessellationEdges}
                                onChange={() => setHideTessellationEdges(!hideTessellationEdges)}
                            />
                            <span>
                                Triangles <span className="text-[10px] uppercase tracking-wide text-amber-300">(reload)</span>
                            </span>
                        </label>
                    </div>

                    {/* Thresholds — edit then Rescan. */}
                    <label className="flex items-center space-x-2 text-sm">
                        <span className="w-40">Thin-triangle aspect ≥</span>
                        <input
                            type="range" min={1} max={50} step={1}
                            value={spikeAspectMin}
                            onChange={(e) => setSpikeAspectMin(parseFloat(e.target.value))}
                            className="flex-1 no-drag"
                        />
                        <input
                            type="number" min={1} max={50} step={1}
                            value={spikeAspectMin}
                            onChange={(e) => setSpikeAspectMin(parseFloat(e.target.value) || 1)}
                            className="w-20 bg-gray-700 text-white p-1 rounded-sm no-drag"
                        />
                    </label>
                    <label className="flex items-center space-x-2 text-sm">
                        <span className="w-40">Outlier vertex K ≥</span>
                        <input
                            type="range" min={1} max={20} step={0.5}
                            value={spikeOutlierK}
                            onChange={(e) => setSpikeOutlierK(parseFloat(e.target.value))}
                            className="flex-1 no-drag"
                        />
                        <input
                            type="number" min={1} max={20} step={0.5}
                            value={spikeOutlierK}
                            onChange={(e) => setSpikeOutlierK(parseFloat(e.target.value) || 1)}
                            className="w-20 bg-gray-700 text-white p-1 rounded-sm no-drag"
                        />
                    </label>
                    <div className="flex items-center gap-2">
                        <button
                            className="bg-blue-700 pointer-fine:hover:bg-blue-600 text-white px-3 py-1 rounded-sm no-drag disabled:opacity-50"
                            onClick={() => void rescan()}
                            disabled={scanning}
                        >
                            {scanning ? "Scanning…" : "Rescan"}
                        </button>
                        <button
                            className="bg-gray-700 pointer-fine:hover:bg-gray-600 text-white px-3 py-1 rounded-sm no-drag"
                            onClick={resetThresholds}
                            title="Reset thresholds to the gallery defaults"
                        >
                            Reset
                        </button>
                        <label className="flex items-center space-x-1 ml-auto">
                            <input type="checkbox" className="no-drag" checked={isolate} onChange={() => setIsolate((v) => !v)}/>
                            <span>Isolate</span>
                        </label>
                        <button
                            className="bg-gray-700 pointer-fine:hover:bg-gray-600 text-white px-2 py-1 rounded-sm no-drag"
                            onClick={() => {
                                setSelectedRange(null);
                                endGeomWalk();
                            }}
                            title="Clear selection / un-isolate"
                        >
                            Clear
                        </button>
                    </div>
                </div>
            </CollapsibleSection>

            {/* Results table — collapsible, default collapsed; y-overflow, sortable, distortion-sorted. */}
            <CollapsibleSection
                title={`Distorted geoms${scanned ? ` (${rows.length})` : ""}${scanning ? " — scanning…" : ""}`}
                defaultOpen={false}
            >
                {rows.length === 0 ? (
                    <div className="text-sm text-gray-400 py-4 text-center">
                        {scanning ? "Scanning scene…" : "No distorted geoms at these thresholds."}
                    </div>
                ) : (
                    <div className="max-h-[50vh] overflow-y-auto border border-gray-700 rounded-sm">
                        <table className="w-full text-xs border-collapse">
                            <thead>
                                <tr>
                                    <th className={thClass} onClick={() => onSort("name")}>Geom{sortArrow("name")}</th>
                                    <th className={`${thClass} text-right`} onClick={() => onSort("spike")}>Spike{sortArrow("spike")}</th>
                                    <th className={`${thClass} text-right`} onClick={() => onSort("spikeTris")}>Spike tris{sortArrow("spikeTris")}</th>
                                    <th className={`${thClass} text-right`} onClick={() => onSort("triangles")}>Tris{sortArrow("triangles")}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {sorted.map((r) => (
                                    <tr
                                        key={`${r.mesh.unique_key}|${r.rangeId}`}
                                        className={`cursor-pointer pointer-fine:hover:bg-gray-700 ${
                                            selectedRange === r.rangeId ? "bg-blue-900" : ""
                                        }`}
                                        onClick={() => onRowClick(r)}
                                    >
                                        <td className="px-2 py-1 max-w-56 truncate" title={r.name}>{r.name}</td>
                                        <td className="px-2 py-1 text-right font-mono">{num(r.spike)}</td>
                                        <td className="px-2 py-1 text-right font-mono">{r.spikeTris}</td>
                                        <td className="px-2 py-1 text-right font-mono">{r.triangles}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </CollapsibleSection>
        </div>
    );
};

export default MeshDistortionSection;
