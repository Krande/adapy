// Tabular view alongside SimulationControls. Two presentation modes:
//
//   1. Streaming-FEA session (the new path): rows = nodes, columns =
//      the active field's components plus a magnitude column for
//      vector fields. Reads the parsed AFBL blob from feaFieldBlob's
//      cache (already populated by load_fea_streaming) so opening the
//      panel is free after the first apply, and tracks
//      ``stepIndex`` / ``fieldName`` / ``reduction`` from
//      feaAnimationStore so the table follows the controls.
//
//   2. Legacy GLB SimulationDataExtensionMetadata: kept as-is for the
//      old non-streaming path so existing fixtures still render their
//      software / version / step / field metadata.
//
// Virtualization: real FEA meshes hit ~50k+ nodes; rendering one DOM
// row each kills frame budget. ``@tanstack/react-virtual`` windows the
// scroll, keeping the header sticky and only mounting visible rows.

import React, {useEffect, useMemo, useRef, useState} from "react";
import {useVirtualizer} from "@tanstack/react-virtual";

import {simulationDataRef} from "@/state/refs";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {fetchFieldBlob, ParsedFeaFieldBlob} from "@/services/feaFieldBlob";
import type {SimulationDataExtensionMetadata, FieldObject} from "@/extensions/design_and_analysis_extension";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";

const ROW_HEIGHT_PX = 24;

export default function SimulationDataInfoPanel() {
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    if (sessionActive) return <FeaNodalDataPanel/>;
    return <LegacyGltfSimDataPanel/>;
}

// ── Streaming-FEA: nodal table ────────────────────────────────────────

const FeaNodalDataPanel: React.FC = () => {
    const {manifest, sourceName, fieldName, reduction, stepIndex} =
        useFeaAnimationStore();
    const scope = useScopeStore((s) => s.current);

    const activeField = useMemo<FeaManifestField | null>(() => {
        if (!manifest || !fieldName) return null;
        return manifest.fields.find((f) => f.name_canonical === fieldName) ?? null;
    }, [manifest, fieldName]);

    if (!manifest || !sourceName || !activeField) {
        return (
            <PanelShell>
                <h2 className="text-lg font-semibold text-gray-800">
                    No FEA field selected
                </h2>
            </PanelShell>
        );
    }

    return (
        <PanelShell>
            <FeaTableHeader
                manifest={manifest}
                sourceName={sourceName}
                field={activeField}
                stepIndex={stepIndex}
                reduction={reduction}
            />
            <FeaNodalTable
                scopeUrl={scopeUrlPart(scope)}
                sourceName={sourceName}
                field={activeField}
                stepIndex={stepIndex}
                reduction={reduction}
            />
        </PanelShell>
    );
};

const FeaTableHeader: React.FC<{
    manifest: FeaManifest;
    sourceName: string;
    field: FeaManifestField;
    stepIndex: number;
    reduction: string;
}> = ({manifest, sourceName, field, stepIndex, reduction}) => {
    const stepLabel = field.steps[stepIndex]?.label ?? `${stepIndex + 1}`;
    return (
        <div className="text-xs text-gray-700 mb-2 space-y-0.5">
            <div className="font-mono truncate" title={sourceName}>
                {sourceName}
            </div>
            <div>
                <span className="text-gray-500">Field:</span>{" "}
                {field.name_canonical}
                <span className="text-gray-400"> ({field.name_native})</span>
                {" · "}
                <span className="text-gray-500">Step:</span> {stepLabel}
                {" · "}
                <span className="text-gray-500">Comp:</span> {reduction}
                {" · "}
                <span className="text-gray-500">Nodes:</span> {manifest.mesh.n_points}
            </div>
        </div>
    );
};

const FeaNodalTable: React.FC<{
    scopeUrl: string;
    sourceName: string;
    field: FeaManifestField;
    stepIndex: number;
    reduction: string;
}> = ({scopeUrl, sourceName, field, stepIndex, reduction}) => {
    const [blob, setBlob] = useState<ParsedFeaFieldBlob | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    // Fetch (or hit the cache) on mount + whenever the field changes.
    // Step changes don't refetch — all steps live in one blob.
    useEffect(() => {
        let cancelled = false;
        setError(null);
        setLoading(true);
        fetchFieldBlob(scopeUrl, sourceName, field)
            .then((b) => {
                if (cancelled) return;
                setBlob(b);
                setLoading(false);
            })
            .catch((err) => {
                if (cancelled) return;
                setError(String(err?.message ?? err));
                setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [scopeUrl, sourceName, field.name_canonical, field.blob?.url]);

    const isVector = field.kind.startsWith("vector");
    const components = field.components;
    const n_components = components.length;
    const headerCols = useMemo(() => {
        const cols = ["Node", ...components];
        if (isVector) cols.push("|·|");
        return cols;
    }, [components, isVector]);

    const stepValues = useMemo<Float32Array | null>(() => {
        if (!blob) return null;
        if (stepIndex < 0 || stepIndex >= blob.steps.length) return null;
        return blob.steps[stepIndex];
    }, [blob, stepIndex]);

    const n_points = blob?.header.n_points ?? 0;

    // Filter + sort state. Both live as table-local state (not in
    // the store) since they don't affect the scene — just the table
    // view. ``sort`` is tri-state: null = natural (insertion) order,
    // {col, dir} = sorted. Header clicks cycle null → asc → desc →
    // null on the same column; clicking a different column resets
    // to asc on that column. Filter is a substring match on the
    // 1-based node id rendered as a decimal string.
    const [filter, setFilter] = useState("");
    const [sort, setSort] = useState<{col: number; dir: "asc" | "desc"} | null>(null);

    // Sort/filter pipeline. Both produce a list of source-row
    // indices the virtualizer iterates over; the table row reads
    // ``stepValues`` at that index. Memoised on the input arrays so
    // a step change (which swaps stepValues) re-sorts but a filter
    // keystroke doesn't re-fetch.
    const visibleRowIndices = useMemo<Int32Array>(() => {
        if (n_points === 0) return new Int32Array(0);
        // Filter pass — substring match on the node id ("12" matches
        // 12, 120, 1234, …). Empty filter keeps everything.
        let indices: Int32Array;
        const f = filter.trim();
        if (!f) {
            indices = new Int32Array(n_points);
            for (let i = 0; i < n_points; i++) indices[i] = i;
        } else {
            const buf: number[] = [];
            for (let i = 0; i < n_points; i++) {
                if (String(i + 1).includes(f)) buf.push(i);
            }
            indices = Int32Array.from(buf);
        }

        if (!sort || !stepValues) return indices;

        // Sort pass. Sort key per row:
        //   * col 0 → node id (the index + 1).
        //   * col 1..n_components → component value at that column.
        //   * col n_components + 1 (only for vectors) → magnitude.
        // Magnitude is recomputed inline rather than cached — the
        // CPU cost of one sqrt per row is negligible next to the
        // sort and saves us a per-step magnitude buffer.
        const {col, dir} = sort;
        const sign = dir === "asc" ? 1 : -1;
        const isMagCol = isVector && col === n_components + 1;
        const compIdx = col - 1; // -1 for node-id col, 0..n_components-1 for comp cols
        // Materialise sort keys once, then sort an index Array — this
        // avoids closure overhead inside Array.sort's comparator.
        const keys = new Float64Array(indices.length);
        for (let k = 0; k < indices.length; k++) {
            const row = indices[k];
            if (col === 0) {
                keys[k] = row + 1;
            } else if (isMagCol) {
                const off = row * n_components;
                let m = 0;
                for (let c = 0; c < Math.min(n_components, 3); c++) {
                    const v = stepValues[off + c] || 0;
                    m += v * v;
                }
                keys[k] = Math.sqrt(m);
            } else {
                const v = stepValues[row * n_components + compIdx];
                keys[k] = isFinite(v) ? v : 0;
            }
        }
        const sortedOrder = Array.from(indices)
            .map((row, k) => ({row, key: keys[k]}))
            .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0) * sign)
            .map((x) => x.row);
        return Int32Array.from(sortedOrder);
    }, [n_points, filter, sort, stepValues, n_components, isVector]);

    const parentRef = useRef<HTMLDivElement | null>(null);
    const rowVirtualizer = useVirtualizer({
        count: visibleRowIndices.length,
        getScrollElement: () => parentRef.current,
        estimateSize: () => ROW_HEIGHT_PX,
        overscan: 16,
    });

    const onHeaderClick = (col: number) => {
        // Tri-state cycle on the same column; reset to asc on a
        // different column. Node-id (col 0) participates in the
        // cycle — useful for jumping to high/low node IDs after a
        // filter narrows the table.
        setSort((prev) => {
            if (!prev || prev.col !== col) return {col, dir: "asc"};
            if (prev.dir === "asc") return {col, dir: "desc"};
            return null;
        });
    };

    if (error) {
        return (
            <div className="text-xs text-red-600 font-mono whitespace-pre-wrap">
                Failed to load field data: {error}
            </div>
        );
    }
    if (loading || !stepValues) {
        return (
            <div className="text-xs text-gray-500 italic">Loading nodal data…</div>
        );
    }

    // Header + body share one scroll container so vertical AND
    // horizontal scroll move them together — without this the header
    // misaligns on narrow viewports where the body overflows
    // horizontally (mobile). Header is ``sticky top-0`` so vertical
    // scroll keeps it pinned; the inner div carries ``minWidth`` so
    // the body grid overflows the viewport rather than crushing
    // columns to unreadable width.
    const minTableWidth = gridMinWidth(headerCols.length);

    return (
        <div className="flex flex-col flex-1 min-h-0">
            <TableToolbar
                filter={filter}
                onFilter={setFilter}
                idLabel="Node"
                resultCount={visibleRowIndices.length}
                totalCount={n_points}
            />
            <div
                ref={parentRef}
                className="flex-1 overflow-auto border border-gray-200 rounded bg-white"
                style={{maxHeight: 320}}
            >
                <div style={{minWidth: minTableWidth, position: "relative"}}>
                    <FeaTableHead
                        headerCols={headerCols}
                        sort={sort}
                        onHeaderClick={onHeaderClick}
                    />
                    <div
                        style={{
                            height: rowVirtualizer.getTotalSize(),
                            position: "relative",
                        }}
                    >
                        {rowVirtualizer.getVirtualItems().map((vRow) => {
                            const row = visibleRowIndices[vRow.index];
                            const off = row * n_components;
                            return (
                                <FeaTableRow
                                    key={row}
                                    top={vRow.start}
                                    height={vRow.size}
                                    nodeId={row + 1}
                                    values={stepValues}
                                    offset={off}
                                    n_components={n_components}
                                    isVector={isVector}
                                />
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
};

const TableToolbar: React.FC<{
    filter: string;
    onFilter: (v: string) => void;
    idLabel: string;
    resultCount: number;
    totalCount: number;
}> = ({filter, onFilter, idLabel, resultCount, totalCount}) => (
    <div className="flex flex-row items-center gap-2 mb-1 text-xs text-gray-700">
        <span className="text-gray-500">{idLabel}:</span>
        <input
            type="text"
            inputMode="numeric"
            placeholder="filter…"
            value={filter}
            onChange={(e) => onFilter(e.target.value)}
            className="border border-gray-300 rounded px-1 py-0.5 w-24 font-mono"
        />
        {filter && (
            <button
                className="text-gray-500 hover:text-gray-800 underline"
                onClick={() => onFilter("")}
            >
                clear
            </button>
        )}
        <span className="ml-auto text-gray-400">
            {resultCount === totalCount
                ? `${totalCount} rows`
                : `${resultCount} / ${totalCount}`}
        </span>
    </div>
);

const FeaTableHead: React.FC<{
    headerCols: string[];
    sort: {col: number; dir: "asc" | "desc"} | null;
    onHeaderClick: (col: number) => void;
}> = ({headerCols, sort, onHeaderClick}) => (
    // ``sticky top-0 z-10`` keeps the header visible while the body
    // virtualizer scrolls; combined with the parent's horizontal
    // overflow it also moves left/right in lockstep with the rows.
    <div
        className="grid border-b border-gray-300 bg-gray-100 text-xs font-semibold text-gray-700 sticky top-0 z-10"
        style={{gridTemplateColumns: gridCols(headerCols.length)}}
    >
        {headerCols.map((c, i) => {
            const active = sort && sort.col === i;
            const arrow = active ? (sort!.dir === "asc" ? "▲" : "▼") : "";
            return (
                <button
                    key={c}
                    onClick={() => onHeaderClick(i)}
                    className={
                        "px-2 py-1 border-r border-gray-300 last:border-r-0 truncate " +
                        "text-left hover:bg-gray-200 cursor-pointer flex items-center justify-between gap-1 " +
                        (active ? "bg-blue-50 text-blue-800" : "")
                    }
                    title="Click to sort (asc → desc → off)"
                >
                    <span className="truncate">{c}</span>
                    {arrow && <span className="text-[10px]">{arrow}</span>}
                </button>
            );
        })}
    </div>
);

const FeaTableRow: React.FC<{
    top: number;
    height: number;
    nodeId: number;
    values: Float32Array;
    offset: number;
    n_components: number;
    isVector: boolean;
}> = ({top, height, nodeId, values, offset, n_components, isVector}) => {
    let mag = 0;
    if (isVector) {
        for (let c = 0; c < Math.min(n_components, 3); c++) {
            const v = values[offset + c] || 0;
            mag += v * v;
        }
        mag = Math.sqrt(mag);
    }
    return (
        <div
            className="absolute left-0 right-0 grid text-xs font-mono text-gray-800 odd:bg-white even:bg-gray-50"
            style={{
                top,
                height,
                gridTemplateColumns: gridCols(1 + n_components + (isVector ? 1 : 0)),
            }}
        >
            <div className="px-2 py-0.5 border-b border-r border-gray-200 truncate">
                {nodeId}
            </div>
            {Array.from({length: n_components}, (_, c) => (
                <div
                    key={c}
                    className="px-2 py-0.5 border-b border-r border-gray-200 truncate"
                >
                    {fmt(values[offset + c])}
                </div>
            ))}
            {isVector && (
                <div className="px-2 py-0.5 border-b border-gray-200 truncate">
                    {fmt(mag)}
                </div>
            )}
        </div>
    );
};

// Sticky-header table layout constants. Pulled out so the scroll
// container's ``minWidth`` and the grid template stay in sync;
// without that, the header and body grids would lay out
// independently and drift on horizontal scroll.
const NODE_ID_COL_PX = 70;
const VALUE_COL_MIN_PX = 88;  // ~7 chars of float scientific notation

function gridCols(n: number): string {
    // ``minmax(VALUE_COL_MIN_PX, 1fr)`` is the load-bearing piece:
    // 1fr alone lets columns crush to zero on narrow viewports,
    // making float values unreadable on mobile. Setting a sensible
    // minimum makes the grid overflow its parent instead — combined
    // with the parent's ``overflow-x-auto`` the user gets horizontal
    // scroll rather than truncated values.
    return `${NODE_ID_COL_PX}px repeat(${n - 1}, minmax(${VALUE_COL_MIN_PX}px, 1fr))`;
}

function gridMinWidth(nCols: number): number {
    // Mirror of the gridCols template so the scroll container can
    // pre-reserve the right width and trigger horizontal overflow.
    return NODE_ID_COL_PX + (nCols - 1) * VALUE_COL_MIN_PX;
}

function fmt(v: number | undefined): string {
    if (v === undefined || !isFinite(v)) return "—";
    const abs = Math.abs(v);
    if (abs === 0) return "0";
    if (abs >= 1e4 || abs < 1e-3) return v.toExponential(3);
    return v.toPrecision(5);
}

const PanelShell: React.FC<{children: React.ReactNode}> = ({children}) => (
    <div className="p-3 border rounded-lg shadow-sm bg-white bg-opacity-90 flex flex-col min-h-0 max-h-[420px]">
        {children}
    </div>
);

// ── Legacy GLB-extension path (unchanged) ─────────────────────────────

const LegacyGltfSimDataPanel: React.FC = () => {
    const simData = simulationDataRef.current as SimulationDataExtensionMetadata | null;
    const [selectedStep, setSelectedStep] = useState(0);
    const [selectedField, setSelectedField] = useState(0);

    useEffect(() => {
        if (simData) {
            setSelectedStep(0);
            setSelectedField(0);
        }
    }, [simData]);

    if (!simData) {
        return (
            <PanelShell>
                <h2 className="text-lg font-semibold text-gray-800">
                    No Simulation Loaded
                </h2>
                <p className="text-sm text-gray-500 mt-2">
                    Load a GLB with the ADA simulation metadata extension, or
                    open a streaming-FEA file from storage to view nodal data.
                </p>
            </PanelShell>
        );
    }

    const steps = simData.steps;
    if (!steps || steps.length === 0) {
        return (
            <PanelShell>
                <h2 className="text-lg font-semibold text-gray-800">No Simulation Steps</h2>
            </PanelShell>
        );
    }
    const safeStep = Math.min(Math.max(selectedStep, 0), steps.length - 1);
    const currentStep = steps[safeStep];
    const fields = currentStep?.fields ?? [];
    if (fields.length === 0) {
        return (
            <PanelShell>
                <h2 className="text-lg font-semibold text-gray-800">No Fields in Step</h2>
            </PanelShell>
        );
    }
    const safeField = Math.min(Math.max(selectedField, 0), fields.length - 1);
    const currentField = fields[safeField] as FieldObject;

    return (
        <PanelShell>
            <h2 className="text-xl font-semibold text-gray-800">{simData.name}</h2>
            <p className="text-sm text-gray-500 mt-1">
                {new Date(simData.date).toLocaleString()}
            </p>
            <div className="mt-3 text-sm text-gray-700">
                <div>
                    <strong>Software:</strong> {simData.fea_software}
                </div>
                <div className="mt-1">
                    <strong>Version:</strong> {simData.fea_software_version}
                </div>
            </div>
            <div className="mt-4">
                <div className="flex flex-row gap-4 items-center">
                    <label className="flex items-center text-sm text-gray-700">
                        <span>Step:</span>
                        <select
                            className="ml-2 p-1 border rounded"
                            value={selectedStep}
                            onChange={(e) => {
                                const idx = parseInt(e.target.value, 10);
                                setSelectedStep(idx);
                                setSelectedField(0);
                            }}
                        >
                            {steps.map((step, idx) => (
                                <option key={idx} value={idx}>
                                    Step {idx + 1}: {step.analysis_type}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="flex items-center text-sm text-gray-700">
                        <span>Field:</span>
                        <select
                            className="ml-2 p-1 border rounded"
                            value={selectedField}
                            onChange={(e) => setSelectedField(parseInt(e.target.value, 10))}
                        >
                            {fields.map((f, fi) => (
                                <option key={fi} value={fi}>
                                    {f.name}
                                </option>
                            ))}
                        </select>
                    </label>
                </div>
                <div className="mt-3 p-3 border rounded bg-white text-sm">
                    <div><strong>Name:</strong> {currentField.name}</div>
                    <div><strong>Type:</strong> {currentField.type}</div>
                    <div>
                        <strong>BufferView:</strong> {currentField.data.bufferView}
                        {currentField.data.byteOffset !== undefined && (
                            <span> @ {currentField.data.byteOffset} bytes</span>
                        )}
                    </div>
                </div>
            </div>
        </PanelShell>
    );
};
