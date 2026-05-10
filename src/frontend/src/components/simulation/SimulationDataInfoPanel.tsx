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
    }, [scopeUrl, sourceName, field.name_canonical, field.blob.url]);

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

    const parentRef = useRef<HTMLDivElement | null>(null);
    const rowVirtualizer = useVirtualizer({
        count: n_points,
        getScrollElement: () => parentRef.current,
        estimateSize: () => ROW_HEIGHT_PX,
        overscan: 16,
    });

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

    return (
        <div className="flex flex-col flex-1 min-h-0">
            <FeaTableHead headerCols={headerCols}/>
            <div
                ref={parentRef}
                className="flex-1 overflow-auto border border-gray-200 rounded-b bg-white"
                style={{maxHeight: 320}}
            >
                <div
                    style={{
                        height: rowVirtualizer.getTotalSize(),
                        position: "relative",
                    }}
                >
                    {rowVirtualizer.getVirtualItems().map((vRow) => {
                        const i = vRow.index;
                        const off = i * n_components;
                        return (
                            <FeaTableRow
                                key={i}
                                top={vRow.start}
                                height={vRow.size}
                                nodeId={i + 1}
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
    );
};

const FeaTableHead: React.FC<{headerCols: string[]}> = ({headerCols}) => (
    <div
        className="grid border border-b-0 border-gray-300 bg-gray-100 text-xs font-semibold text-gray-700"
        style={{gridTemplateColumns: gridCols(headerCols.length)}}
    >
        {headerCols.map((c) => (
            <div
                key={c}
                className="px-2 py-1 border-r border-gray-300 last:border-r-0 truncate"
            >
                {c}
            </div>
        ))}
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

function gridCols(n: number): string {
    // 70px node-id column, then equal flex for value columns.
    return `70px repeat(${n - 1}, minmax(0, 1fr))`;
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
