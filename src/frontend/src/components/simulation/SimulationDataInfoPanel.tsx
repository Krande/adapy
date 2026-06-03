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
import {useTableNavStore} from "@/state/tableNavStore";
import {fetchFieldBlob, makeViewerApiFetcher, ParsedFeaFieldBlob} from "@/services/feaFieldBlob";
import {goToNode, clearGoToNode} from "@/utils/scene/fea/goToNode";
import type {SimulationDataExtensionMetadata, FieldObject} from "@/extensions/design_and_analysis_extension";
import type {
    FeaManifest,
    FeaManifestField,
    FeaManifestHistory,
} from "@/services/viewerApi";

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

    // Element fields don't have per-node values — they live on
    // integration points inside each element. The nodal table can't
    // show them; surface a hint so the user knows to switch to a
    // nodal field rather than wonder why the table is empty.
    if (activeField.per_type && activeField.per_type.length > 0) {
        const totalElements = activeField.per_type.reduce(
            (sum, bk) => sum + bk.n_elements,
            0,
        );
        return (
            <PanelShell>
                <FeaTableHeader
                    manifest={manifest}
                    sourceName={sourceName}
                    field={activeField}
                    stepIndex={stepIndex}
                    reduction={reduction}
                />
                <div className="text-sm text-gray-700 mt-3 space-y-2">
                    <p>
                        <span className="font-semibold">{activeField.name_canonical}</span>{" "}
                        is an element field — its values live on integration
                        points inside elements, not on nodes. The mesh is
                        coloured using the active layer + IP reduction shown
                        in the Sim Controls options panel.
                    </p>
                    <p className="text-gray-500">
                        {totalElements.toLocaleString()} elements across{" "}
                        {activeField.per_type.length} type
                        {activeField.per_type.length === 1 ? "" : "s"}
                        {": "}
                        {activeField.per_type.map((bk) => `${bk.elem_type} (${bk.n_elements})`).join(", ")}.
                    </p>
                    <p className="text-gray-500">
                        Pick a nodal field (e.g. displacement, reaction) above
                        to inspect raw per-node values here.
                    </p>
                </div>
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
            {manifest.history && manifest.history.regions.length > 0 && (
                <FeaHistorySection history={manifest.history}/>
            )}
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

    // Row-level "go to in 3D" state. Clicking the eye icon on a row
    // both marks that row's node in the 3D scene (sphere marker +
    // camera frame) and highlights the row visually so the user can
    // keep track of which one's currently scoped.
    const activeNodeId = useTableNavStore((s) => s.activeNodeId);
    const setActiveNodeId = useTableNavStore((s) => s.setActiveNodeId);
    const goToTarget = useTableNavStore((s) => s.goToTarget);
    const setGoToTarget = useTableNavStore((s) => s.setGoToTarget);
    const onGoToNode = (nodeId: number) => {
        if (activeNodeId === nodeId) {
            // Toggle off: second click on the same row clears the
            // spotlight (marker disappears, row de-highlights). The
            // camera frame stays where it is — un-framing back to
            // the prior view would be surprising.
            clearGoToNode();
            setActiveNodeId(null);
            return;
        }
        goToNode(nodeId);
        setActiveNodeId(nodeId);
    };

    // Fetch (or hit the cache) on mount + whenever the field changes.
    // Step changes don't refetch — all steps live in one blob.
    useEffect(() => {
        let cancelled = false;
        setError(null);
        setLoading(true);
        // Build a fetcher for the standalone-viewer's bake-job
        // storage convention; the helper returns both the fetcher and
        // a stable cache key keyed off the (scope, source) tuple.
        ((): Promise<ParsedFeaFieldBlob> => {
            const {fetcher, cacheKey} = makeViewerApiFetcher(scopeUrl, sourceName);
            return fetchFieldBlob(fetcher, field, cacheKey);
        })()
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
    // Last column is the "Go" affordance — empty header label, the
    // row renders an eye-icon button there. Sort handler ignores it
    // (no data to sort on). Kept in headerCols so the grid count
    // matches between header and rows.
    const headerCols = useMemo(() => {
        const cols = ["Node", ...components];
        if (isVector) cols.push("|·|");
        cols.push("");
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

    // External nav requests (Phase 1b: ObjectInfoBoxComponent's
    // "Show in data" button). When ``goToTarget`` becomes non-null,
    // find the row, scroll it into view, mark it active, and clear
    // the target so the same trigger can fire twice in a row.
    //
    // Filter clear: if the user's current filter would hide the
    // target node, the scroll would land nowhere. Auto-clear the
    // filter so the requested node is always visible — the user
    // clicked "Show in data" expecting to see it.
    useEffect(() => {
        if (!goToTarget) return;
        if (goToTarget.kind !== "node") return;
        const targetRow = goToTarget.id - 1; // 1-based id → 0-based row
        if (targetRow < 0 || targetRow >= n_points) {
            setGoToTarget(null);
            return;
        }

        let pos = -1;
        for (let k = 0; k < visibleRowIndices.length; k++) {
            if (visibleRowIndices[k] === targetRow) {
                pos = k;
                break;
            }
        }
        if (pos < 0) {
            // Filter hides the target. Drop it and re-run on the
            // next render — the cleared filter will produce a fresh
            // visibleRowIndices that contains the row.
            if (filter) setFilter("");
            return;
        }

        rowVirtualizer.scrollToIndex(pos, {align: "center"});
        setActiveNodeId(goToTarget.id);
        setGoToTarget(null);
    }, [goToTarget, visibleRowIndices, n_points, filter,
        rowVirtualizer, setActiveNodeId, setGoToTarget]);

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
                className="flex-1 overflow-auto border border-gray-200 rounded-sm bg-white"
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
                            const nodeId = row + 1;
                            return (
                                <FeaTableRow
                                    key={row}
                                    top={vRow.start}
                                    height={vRow.size}
                                    nodeId={nodeId}
                                    values={stepValues}
                                    offset={off}
                                    n_components={n_components}
                                    isVector={isVector}
                                    active={activeNodeId === nodeId}
                                    onGoTo={onGoToNode}
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
            className="border border-gray-300 rounded-sm px-1 py-0.5 w-24 font-mono"
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
            // Empty trailing label is the "Go" affordance column —
            // no data, so no sort. Render a static cell instead of
            // a button so it doesn't accept clicks. ``key`` falls
            // back to index since the empty string would collide.
            if (c === "") {
                return (
                    <div
                        key={`go-${i}`}
                        className="px-2 py-1 border-gray-300"
                        aria-hidden="true"
                    />
                );
            }
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
    active: boolean;
    onGoTo: (nodeId: number) => void;
}> = ({top, height, nodeId, values, offset, n_components, isVector, active, onGoTo}) => {
    let mag = 0;
    if (isVector) {
        for (let c = 0; c < Math.min(n_components, 3); c++) {
            const v = values[offset + c] || 0;
            mag += v * v;
        }
        mag = Math.sqrt(mag);
    }
    // Active row: light-blue background and a subtle left border so
    // it stands out even in the dense alternating-stripe table.
    // ``!important`` not needed because the active class comes after
    // ``odd:`` / ``even:`` in the className string and Tailwind's
    // generated stylesheet keeps them at the same specificity —
    // last-write-wins via source order.
    const rowBg = active
        ? "bg-blue-100 ring-1 ring-inset ring-blue-400"
        : "odd:bg-white even:bg-gray-50";
    // Trailing data-col count for the gridCols template: 1 (id) +
    // n_components + (magnitude?) + 1 (go) — match FeaTableHead.
    const totalCols = 1 + n_components + (isVector ? 1 : 0) + 1;
    return (
        <div
            className={
                "absolute left-0 right-0 grid text-xs font-mono text-gray-800 " + rowBg
            }
            style={{
                top,
                height,
                gridTemplateColumns: gridCols(totalCols),
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
                <div className="px-2 py-0.5 border-b border-r border-gray-200 truncate">
                    {fmt(mag)}
                </div>
            )}
            <button
                className={
                    "px-1 border-b border-gray-200 flex items-center justify-center " +
                    (active
                        ? "text-blue-700 hover:text-blue-900"
                        : "text-gray-400 hover:text-blue-600")
                }
                onClick={() => onGoTo(nodeId)}
                title={
                    active
                        ? `Clear marker for node ${nodeId}`
                        : `Show node ${nodeId} in the 3D scene`
                }
                aria-pressed={active}
            >
                <EyeIcon/>
            </button>
        </div>
    );
};

const EyeIcon: React.FC = () => (
    <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="w-3.5 h-3.5"
        aria-hidden="true"
    >
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
        <circle cx="12" cy="12" r="3"/>
    </svg>
);

// Sticky-header table layout constants. Pulled out so the scroll
// container's ``minWidth`` and the grid template stay in sync;
// without that, the header and body grids would lay out
// independently and drift on horizontal scroll.
const NODE_ID_COL_PX = 70;
const VALUE_COL_MIN_PX = 88;  // ~7 chars of float scientific notation
const GO_COL_PX = 36;         // square button at row end

function gridCols(n: number): string {
    // ``minmax(VALUE_COL_MIN_PX, 1fr)`` is the load-bearing piece:
    // 1fr alone lets columns crush to zero on narrow viewports,
    // making float values unreadable on mobile. Setting a sensible
    // minimum makes the grid overflow its parent instead — combined
    // with the parent's ``overflow-x-auto`` the user gets horizontal
    // scroll rather than truncated values. Trailing ``GO_COL_PX``
    // hosts the eye-icon "go to in 3D" button per row; in the
    // header it's a static label slot.
    return (
        `${NODE_ID_COL_PX}px ` +
        `repeat(${n - 2}, minmax(${VALUE_COL_MIN_PX}px, 1fr)) ` +
        `${GO_COL_PX}px`
    );
}

function gridMinWidth(nCols: number): number {
    // Mirror of the gridCols template so the scroll container can
    // pre-reserve the right width and trigger horizontal overflow.
    return NODE_ID_COL_PX + (nCols - 2) * VALUE_COL_MIN_PX + GO_COL_PX;
}

function fmt(v: number | undefined): string {
    if (v === undefined || !isFinite(v)) return "—";
    const abs = Math.abs(v);
    if (abs === 0) return "0";
    if (abs >= 1e4 || abs < 1e-3) return v.toExponential(3);
    return v.toPrecision(5);
}

// History output — sparse time-series at monitored points. Field
// output is a 3D paint; history output is a per-frame value at a
// hand-picked node/element/model region. v1 keeps the layout flat:
// three dropdowns (Region / Variable / Step) drive a small (time,
// value) table. The future graph view is tracked in
// project-data-table-graph but lands after this section is live.
const FeaHistorySection: React.FC<{history: FeaManifestHistory}> = ({history}) => {
    const [regionId, setRegionId] = useState<string>(
        history.regions[0]?.id ?? "",
    );
    const [variable, setVariable] = useState<string>(
        history.variables[0]?.name_native ?? "",
    );
    const [stepIdx, setStepIdx] = useState<number>(
        history.steps[0]?.i ?? 0,
    );

    // Match the active series by all three keys. A missing match is
    // expected when, e.g., a node region has only U1 recorded but the
    // user picks ALLAE — surface that case in the table area.
    const series = useMemo(() => {
        return history.series.find(
            (s) =>
                s.region_id === regionId &&
                s.variable === variable &&
                s.step_idx === stepIdx,
        ) ?? null;
    }, [history.series, regionId, variable, stepIdx]);

    const region = history.regions.find((r) => r.id === regionId) ?? null;
    const variableMeta = history.variables.find(
        (v) => v.name_native === variable,
    ) ?? null;

    return (
        <div className="mt-3 pt-3 border-t border-gray-300">
            <div className="text-xs font-semibold text-gray-700 mb-2">
                History output
                <span className="ml-2 font-normal text-gray-500">
                    ({history.regions.length} region
                    {history.regions.length === 1 ? "" : "s"}, {" "}
                    {history.variables.length} variable
                    {history.variables.length === 1 ? "" : "s"})
                </span>
            </div>
            <div className="flex flex-wrap gap-2 mb-2 text-xs text-gray-700">
                <label className="flex items-center gap-1 min-w-0 flex-1 sm:flex-none">
                    <span className="text-gray-500 shrink-0">Region:</span>
                    <select
                        className="border border-gray-300 rounded-sm px-1 py-0.5 font-mono min-w-0 flex-1 sm:flex-none truncate sm:max-w-56"
                        value={regionId}
                        onChange={(e) => setRegionId(e.target.value)}
                    >
                        {history.regions.map((r) => (
                            <option key={r.id} value={r.id}>
                                [{r.kind}] {r.display_name || r.label}
                            </option>
                        ))}
                    </select>
                </label>
                <label className="flex items-center gap-1 min-w-0 flex-1 sm:flex-none">
                    <span className="text-gray-500 shrink-0">Variable:</span>
                    <select
                        className="border border-gray-300 rounded-sm px-1 py-0.5 font-mono min-w-0 flex-1 sm:flex-none truncate sm:max-w-40"
                        value={variable}
                        onChange={(e) => setVariable(e.target.value)}
                    >
                        {history.variables.map((v) => (
                            <option key={v.name_native} value={v.name_native}>
                                {v.name_native}
                                {v.component ? ` (${v.component})` : ""}
                            </option>
                        ))}
                    </select>
                </label>
                <label className="flex items-center gap-1 min-w-0 flex-1 sm:flex-none">
                    <span className="text-gray-500 shrink-0">Step:</span>
                    <select
                        className="border border-gray-300 rounded-sm px-1 py-0.5 font-mono min-w-0 flex-1 sm:flex-none truncate sm:max-w-40"
                        value={stepIdx}
                        onChange={(e) => setStepIdx(parseInt(e.target.value, 10))}
                    >
                        {history.steps.map((s) => (
                            <option key={s.i} value={s.i}>
                                {s.name}
                            </option>
                        ))}
                    </select>
                </label>
            </div>

            {region && variableMeta && (
                <div className="text-[11px] text-gray-500 mb-1">
                    {region.instance && (
                        <span className="font-mono">{region.instance}</span>
                    )}
                    {region.coords && (
                        <span className="ml-2 font-mono">
                            ({region.coords.map((c) => c.toFixed(3)).join(", ")})
                        </span>
                    )}
                    {variableMeta.category !== "other" && (
                        <span className="ml-2">{variableMeta.category}</span>
                    )}
                    {variableMeta.unit && (
                        <span className="ml-2">[{variableMeta.unit}]</span>
                    )}
                </div>
            )}

            {!series ? (
                <div className="text-xs italic text-gray-500">
                    No samples for this combination.
                </div>
            ) : (
                <FeaHistoryTable times={series.times} values={series.values}/>
            )}
        </div>
    );
};

const FeaHistoryTable: React.FC<{
    times: number[];
    values: number[];
}> = ({times, values}) => {
    // The series array is short by construction (a few hundred frames
    // at most for typical analyses), so no virtualization — a plain
    // overflow-y-auto with a capped height keeps it light.
    return (
        <div
            className="border border-gray-200 rounded-sm bg-white overflow-auto"
            style={{maxHeight: 160}}
        >
            <div
                className="grid border-b border-gray-300 bg-gray-100 text-xs font-semibold text-gray-700 sticky top-0 z-10"
                style={{gridTemplateColumns: "minmax(80px, 1fr) minmax(120px, 2fr)"}}
            >
                <div className="px-2 py-1 border-r border-gray-300">Time</div>
                <div className="px-2 py-1">Value</div>
            </div>
            {times.map((t, i) => (
                <div
                    key={i}
                    className="grid text-xs font-mono text-gray-800 odd:bg-white even:bg-gray-50"
                    style={{gridTemplateColumns: "minmax(80px, 1fr) minmax(120px, 2fr)"}}
                >
                    <div className="px-2 py-0.5 border-b border-r border-gray-200 truncate">
                        {fmt(t)}
                    </div>
                    <div className="px-2 py-0.5 border-b border-gray-200 truncate">
                        {fmt(values[i])}
                    </div>
                </div>
            ))}
        </div>
    );
};

const PanelShell: React.FC<{children: React.ReactNode}> = ({children}) => (
    <div className="p-3 border rounded-lg shadow-xs bg-white bg-opacity-90 flex flex-col min-h-0 max-h-[420px] overflow-auto">
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
                            className="ml-2 p-1 border rounded-sm"
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
                            className="ml-2 p-1 border rounded-sm"
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
                <div className="mt-3 p-3 border rounded-sm bg-white text-sm">
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
