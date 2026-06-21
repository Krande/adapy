import React, {useEffect, useMemo, useRef} from "react";

import {useCapacityResultsStore} from "@/state/capacityResultsStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {
    applyCapacityDefinitionView,
    applyCapacityIsolation,
    applyCapacitySelectionHighlight,
    applyCapacityVisualField,
    clearCapacityDefinitionView,
    clearCapacityIsolation,
    clearCapacityVisualField,
} from "@/utils/scene/handlers/load_fea_streaming";

const CapacityControls: React.FC = () => {
    const {
        results,
        activeRunId,
        activeCaseId,
        showDefinitions,
        showResults,
        isolateDefinitions,
        activeMetricId,
        selectedModelId,
        selectedResultId,
        failedOnly,
        loading,
        error,
        setActiveRunId,
        setActiveCaseId,
        setShowDefinitions,
        setShowResults,
        setIsolateDefinitions,
        setActiveMetricId,
        setSelectedModelId,
        setSelectedCapacityResult,
        setFailedOnly,
    } = useCapacityResultsStore();
    const pickedName = useObjectInfoStore((s) => s.name);
    const pickedFaceIndex = useObjectInfoStore((s) => s.faceIndex);
    const pickedFileName = useObjectInfoStore((s) => s.fileName);
    const lastHandledPickKeyRef = useRef<string | null>(null);

    const run = useMemo(() => {
        if (!results?.runs?.length) return null;
        return results.runs.find((r) => r.id === activeRunId) ?? results.runs[0];
    }, [results, activeRunId]);

    const rows = useMemo(() => {
        if (!run || !activeCaseId) return [];
        const base = run.case_results
            .filter((row) => row.case_id === activeCaseId)
            .filter((row) => !failedOnly || !row.passed)
            .sort((a, b) => (b.governing_usage ?? -1) - (a.governing_usage ?? -1));
        return base;
    }, [run, activeCaseId, failedOnly]);

    const selectedRow = useMemo(() => {
        if (!run || !activeCaseId) return null;
        const activeRows = run.case_results.filter((row) => row.case_id === activeCaseId);
        if (selectedResultId) {
            const resultMatch = activeRows.find((row) => caseResultKey(row) === selectedResultId);
            if (resultMatch) return resultMatch;
        }
        if (!selectedModelId) return null;
        return activeRows
            .filter((row) => row.capacity_model_id === selectedModelId)
            .sort((a, b) => (b.governing_usage ?? -1) - (a.governing_usage ?? -1))[0] ?? null;
    }, [run, selectedModelId, selectedResultId, activeCaseId]);

    useEffect(() => {
        if (!run) return;
        if (showDefinitions) {
            applyCapacityDefinitionView();
        } else {
            clearCapacityDefinitionView();
        }
        if (showResults && activeCaseId) {
            applyCapacityVisualField(activeMetricId, activeCaseId);
        } else {
            clearCapacityVisualField();
        }
        applyCapacitySelectionHighlight();
    }, [run, activeCaseId, activeMetricId, showDefinitions, showResults, selectedModelId]);

    useEffect(() => {
        if (!run) return;
        if (isolateDefinitions) {
            applyCapacityIsolation();
        } else {
            clearCapacityIsolation();
        }
    }, [run, isolateDefinitions]);

    useEffect(() => {
        const pickKey = pickedName ? `${pickedFileName ?? ""}:${pickedName}:${pickedFaceIndex ?? ""}` : null;
        if (!pickKey) {
            lastHandledPickKeyRef.current = null;
            return;
        }
        if (lastHandledPickKeyRef.current === pickKey) return;
        lastHandledPickKeyRef.current = pickKey;
        if (!run || !pickedName || !showDefinitions) return;
        const elementId = elementIdFromName(pickedName);
        if (elementId == null) return;
        const {
            activeCaseId: currentCaseId,
            activeMetricId: currentMetricId,
            selectedModelId: currentSelectedModelId,
        } = useCapacityResultsStore.getState();
        const match = pickCapacityModelForElement(
            run,
            elementId,
            currentCaseId,
            currentMetricId,
            currentSelectedModelId,
        );
        if (match && match.id !== currentSelectedModelId) {
            setSelectedModelId(match.id);
        }
    }, [run, pickedName, pickedFaceIndex, pickedFileName, showDefinitions, setSelectedModelId]);

    if (!results && !loading && !error) return null;

    return (
        <div className="rounded-sm border border-gray-700 bg-gray-900/95 text-gray-100 text-xs shadow-lg">
            <div className="flex items-center justify-between gap-2 border-b border-gray-700 px-3 py-2">
                <div className="font-semibold tracking-wide">Capacity</div>
                {loading && <div className="text-gray-400">Loading</div>}
                {error && <div className="text-red-300 truncate max-w-[220px]">{error}</div>}
            </div>
            {run && (
                <div className="p-3 space-y-3">
                    <div className="grid grid-cols-2 gap-2">
                        <label className="flex flex-col gap-1">
                            <span className="text-gray-400">Run</span>
                            <select
                                className="bg-gray-800 border border-gray-600 rounded-sm px-2 py-1"
                                value={run.id}
                                onChange={(e) => setActiveRunId(e.target.value)}
                            >
                                {results!.runs.map((r) => (
                                    <option key={r.id} value={r.id}>{r.label ?? r.id}</option>
                                ))}
                            </select>
                        </label>
                        <label className="flex flex-col gap-1">
                            <span className="text-gray-400">Case</span>
                            <select
                                className="bg-gray-800 border border-gray-600 rounded-sm px-2 py-1"
                                value={activeCaseId ?? ""}
                                onChange={(e) => setActiveCaseId(e.target.value)}
                            >
                                {run.result_cases.map((c) => (
                                    <option key={c.id} value={c.id}>{c.label ?? c.id}</option>
                                ))}
                            </select>
                        </label>
                    </div>

                    <div className="flex items-center gap-1">
                        <button
                            className={modeButton(showDefinitions)}
                            onClick={() => setShowDefinitions(!showDefinitions)}
                        >
                            Show definitions
                        </button>
                        <button
                            className={modeButton(showResults)}
                            onClick={() => setShowResults(!showResults)}
                        >
                            Results
                        </button>
                        <button
                            className={modeButton(isolateDefinitions)}
                            onClick={() => setIsolateDefinitions(!isolateDefinitions)}
                            title="Hide everything except the capacity models"
                        >
                            Only definitions
                        </button>
                    </div>

                    <div className="grid grid-cols-[1fr_auto] gap-2 items-end">
                        <label className="flex flex-col gap-1">
                            <span className="text-gray-400">Metric</span>
                            <select
                                className="bg-gray-800 border border-gray-600 rounded-sm px-2 py-1"
                                value={activeMetricId}
                                onChange={(e) => setActiveMetricId(e.target.value)}
                                disabled={!showResults}
                            >
                                {run.visual_fields.map((field) => (
                                    <option key={field.id} value={field.id}>{metricLabel(run, field)}</option>
                                ))}
                            </select>
                        </label>
                        <label className="inline-flex items-center gap-2 pb-1 text-gray-300">
                            <input
                                type="checkbox"
                                checked={failedOnly}
                                onChange={(e) => setFailedOnly(e.target.checked)}
                            />
                            Failed
                        </label>
                    </div>

                    {showResults && <CapacityLegend />}

                    <div className="max-h-64 overflow-y-auto border border-gray-700 rounded-sm">
                        <table className="w-full table-fixed text-left">
                            <thead className="sticky top-0 bg-gray-800 text-gray-300">
                                <tr>
                                    <th className="px-2 py-1 w-[48%]">Model</th>
                                    <th className="px-2 py-1 w-[22%]">UF</th>
                                    <th className="px-2 py-1">Check</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => {
                                    const rowKey = caseResultKey(row);
                                    const selected = selectedRow ? caseResultKey(selectedRow) === rowKey : false;
                                    return (
                                        <tr
                                            key={rowKey}
                                            className={
                                                "cursor-pointer border-t border-gray-800 hover:bg-gray-800 " +
                                                (selected ? "bg-gray-800" : "")
                                            }
                                            onClick={() => setSelectedCapacityResult(row.capacity_model_id, rowKey)}
                                        >
                                            <td className="px-2 py-1 truncate" title={row.stiffener ?? row.panel_group}>
                                                {shortName(row.stiffener ?? row.panel_group)}
                                            </td>
                                            <td className={ufClass(row.governing_usage)}>
                                                {formatUf(row.governing_usage)}
                                            </td>
                                            <td className="px-2 py-1 truncate" title={row.governing_check ?? ""}>
                                                {row.governing_check ?? ""}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    {selectedRow && (
                        <div className="border border-gray-700 rounded-sm p-2 space-y-1">
                            <div className="font-semibold truncate" title={selectedRow.capacity_model_id}>
                                {shortName(selectedRow.stiffener ?? selectedRow.panel_group)}
                            </div>
                            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-gray-300">
                                <div>UF</div>
                                <div className={ufClass(selectedRow.governing_usage)}>
                                    {formatUf(selectedRow.governing_usage)}
                                </div>
                                <div>Clause</div>
                                <div>{selectedRow.governing_clause ?? ""}</div>
                                <div>Status</div>
                                <div>{selectedRow.passed ? "OK" : "FAIL"}</div>
                            </div>
                            <div className="pt-1 space-y-1">
                                {selectedRow.checks.slice(0, 4).map((check) => (
                                    <div key={check.id} className="grid grid-cols-[1fr_auto] gap-x-2 text-gray-300">
                                        <span className="truncate" title={check.label}>{check.label}</span>
                                        <span className={ufClass(check.usage)}>{formatUf(check.usage)}</span>
                                        <span className="col-span-2 text-[10px] text-gray-500 truncate" title={formulaReference(check)}>
                                            {formulaReference(check)}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

const CapacityLegend: React.FC = () => (
    <div className="space-y-1">
        <div className="h-2 rounded-sm capacity-uf-gradient" />
        <div className="relative h-3 text-[10px] text-gray-400">
            <span className="absolute left-0">0.0</span>
            <span className="absolute left-1/2 -translate-x-1/2">0.6</span>
            <span className="absolute left-[66.6667%] -translate-x-1/2">0.8</span>
            <span className="absolute left-[83.3333%] -translate-x-1/2">1.0+</span>
        </div>
    </div>
);

function modeButton(active: boolean): string {
    return (
        "px-2 py-1 rounded-sm border " +
        (active
            ? "bg-blue-600 border-blue-500 text-white"
            : "bg-gray-800 border-gray-600 text-gray-300 hover:bg-gray-700")
    );
}

function formatUf(value: number | null | undefined): string {
    if (value == null || !isFinite(value)) return "-";
    return value.toFixed(3);
}

function ufClass(value: number | null | undefined): string {
    const base = "px-2 py-1 font-mono ";
    if (value == null || !isFinite(value)) return base + "text-gray-400";
    if (value > 1.0) return base + "text-red-300";
    if (value >= 0.8) return base + "text-yellow-200";
    return base + "text-gray-100";
}

function shortName(name: string): string {
    return name
        .replace(/^panelGroup\(/, "")
        .replace(/\)$/, "")
        .replace(/^Stiffener_/, "");
}

type CapacityRunLike = NonNullable<ReturnType<typeof useCapacityResultsStore.getState>["results"]>["runs"][number];
type CapacityCaseResultLike = CapacityRunLike["case_results"][number];
type CapacityVisualFieldLike = CapacityRunLike["visual_fields"][number];

function caseResultKey(row: CapacityCaseResultLike): string {
    return row.id ?? `${row.case_id}::${row.capacity_model_id}::${row.stiffener ?? row.panel_group}`;
}

function metricLabel(run: CapacityRunLike, field: CapacityVisualFieldLike): string {
    const ref = metricReference(run, field);
    return ref ? `${field.label} ${ref}` : field.label;
}

function metricReference(run: CapacityRunLike, field: CapacityVisualFieldLike): string {
    const equations = field.equations?.filter(Boolean);
    if (equations?.length) return equations.join(", ");
    if (field.clause) return `(${field.clause})`;
    const checkId = field.check_id ?? field.id.replace(/^capacity\.uf\./, "");
    const check = run.check_catalog?.find((entry) => entry.id === checkId);
    if (check?.equations?.length) return check.equations.join(", ");
    if (check?.clause) return `(${check.clause})`;
    return "";
}

function pickCapacityModelForElement(
    run: CapacityRunLike,
    elementId: number,
    activeCaseId: string | null,
    activeMetricId: string,
    selectedModelId: string | null,
) {
    const candidates = run.capacity_models.filter((model) =>
        (model.element_ids.all ?? []).includes(elementId),
    );
    if (candidates.length === 0) return null;
    const stillSelected = candidates.find((model) => model.id === selectedModelId);
    if (stillSelected) return stillSelected;

    const scoreByModel = activeMetricScores(run, activeCaseId, activeMetricId);
    return candidates
        .slice()
        .sort((a, b) => {
            const scoreDiff = (scoreByModel.get(b.id) ?? -Infinity) - (scoreByModel.get(a.id) ?? -Infinity);
            if (scoreDiff !== 0) return scoreDiff;
            return (a.element_ids.all?.length ?? Number.MAX_SAFE_INTEGER)
                - (b.element_ids.all?.length ?? Number.MAX_SAFE_INTEGER);
        })[0] ?? null;
}

function activeMetricScores(
    run: CapacityRunLike,
    activeCaseId: string | null,
    activeMetricId: string,
): Map<string, number> {
    const out = new Map<string, number>();
    const caseId = activeCaseId ?? run.result_cases[0]?.id ?? run.case_results[0]?.case_id;
    if (!caseId) return out;

    const field = run.visual_fields.find((f) => f.id === activeMetricId);
    const fieldCase = field?.cases.find((c) => c.case_id === caseId);
    if (fieldCase) {
        for (const value of fieldCase.values) {
            if (!value.capacity_model_id || value.value == null || !isFinite(value.value)) continue;
            const previous = out.get(value.capacity_model_id);
            if (previous == null || value.value > previous) out.set(value.capacity_model_id, value.value);
        }
        return out;
    }

    for (const row of run.case_results) {
        if (row.case_id === caseId && row.governing_usage != null) {
            const previous = out.get(row.capacity_model_id);
            if (previous == null || row.governing_usage > previous) {
                out.set(row.capacity_model_id, row.governing_usage);
            }
        }
    }
    return out;
}

function formulaReference(check: {clause?: string; equations?: string[]}): string {
    const clause = check.clause ? `DNV-RP-C201 ${check.clause}` : "DNV-RP-C201";
    const equations = check.equations?.length ? ` ${check.equations.join(", ")}` : "";
    return `${clause}${equations}`;
}

function elementIdFromName(name: string): number | null {
    const match = /^E(\d+)$/.exec(name.trim());
    if (!match) return null;
    const parsed = Number.parseInt(match[1], 10);
    return Number.isFinite(parsed) ? parsed : null;
}

export default CapacityControls;
