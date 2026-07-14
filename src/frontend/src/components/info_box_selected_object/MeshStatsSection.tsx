// Collapsible "Mesh" section for the selection inspector — tessellation
// stats for the currently-selected geom(s), computed client-side from
// the batched geometry buffer (no server round-trip). Aggregates across
// a multi-selection: total triangles / vertices / surface area, and the
// mesh density (triangles per m²) that the gallery "density" walk sorts
// by. Renders nothing when the selection has no triangle geometry.

import React, {useState} from "react";

import {useViewerStores} from "@/state/AdaViewerContext";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {computeRangeStats} from "@/utils/mesh_select/meshStats";

const Chevron: React.FC<{open: boolean}> = ({open}) => (
    <svg
        width="10"
        height="10"
        viewBox="0 0 10 10"
        aria-hidden="true"
        className={`transition-transform ${open ? "rotate-90" : ""}`}
    >
        <path d="M3 1l4 4-4 4" stroke="currentColor" strokeWidth="1.4" fill="none" />
    </svg>
);

const fmtInt = (v: number): string => Math.round(v).toLocaleString();

// Compact SI-ish number: keep 3 significant figures without scientific
// notation for the everyday range.
const fmtNum = (v: number): string => {
    if (!isFinite(v) || v === 0) return "0";
    const abs = Math.abs(v);
    if (abs >= 1000 || abs < 0.001) return v.toPrecision(3);
    return v.toFixed(abs >= 100 ? 1 : abs >= 1 ? 2 : 3);
};

const Row: React.FC<{label: string; children: React.ReactNode}> = ({label, children}) => (
    <tr>
        <td className="pr-3 text-gray-500 align-top whitespace-nowrap">{label}</td>
        <td className="font-mono text-gray-200">{children}</td>
    </tr>
);

interface Agg {
    geoms: number;
    triangles: number;
    vertices: number;
    area: number;
    volume: number;
}

const MeshStatsSection: React.FC = () => {
    const {useSelectedObjectStore} = useViewerStores();
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const [expanded, setExpanded] = useState(false);

    const agg: Agg = {geoms: 0, triangles: 0, vertices: 0, area: 0, volume: 0};
    selectedObjects.forEach((rangeIds, obj) => {
        if (!(obj instanceof CustomBatchedMesh)) return;
        rangeIds.forEach((rangeId) => {
            const s = computeRangeStats(obj, rangeId);
            if (!s) return;
            agg.geoms += 1;
            agg.triangles += s.triangles;
            agg.vertices += s.vertices;
            agg.area += s.area;
            agg.volume += s.volume;
        });
    });

    if (agg.geoms === 0) return null;

    const density = agg.area > 0 ? agg.triangles / agg.area : 0;

    return (
        <div className="mt-2">
            <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="flex items-center gap-1 text-[12px] text-gray-100 hover:text-white"
                aria-expanded={expanded}
                aria-controls="object-mesh-stats"
            >
                <Chevron open={expanded} />
                <span className="font-semibold">Mesh ({fmtInt(agg.triangles)} tris)</span>
            </button>
            {expanded && (
                <div id="object-mesh-stats" className="mt-1 ml-4">
                    <table className="text-[11px] border-separate border-spacing-y-0.5">
                        <tbody>
                            {agg.geoms > 1 && <Row label="Geoms:">{fmtInt(agg.geoms)}</Row>}
                            <Row label="Triangles:">{fmtInt(agg.triangles)}</Row>
                            <Row label="Vertices:">{fmtInt(agg.vertices)}</Row>
                            <Row label="Surface area:">{fmtNum(agg.area)} m²</Row>
                            <Row label="BBox volume:">{fmtNum(agg.volume)} m³</Row>
                            <Row label="Density:">{fmtNum(density)} tris/m²</Row>
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

export default MeshStatsSection;
