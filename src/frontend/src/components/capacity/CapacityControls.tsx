import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import CapacityResultsPanel from "@/components/capacity/CapacityResultsPanel";
import {
  CAPACITY_FLOATING_PANEL_RIGHT_PX,
  CAPACITY_INPUT_RIGHT_WITH_RESULTS_PX,
  caseLabelForRow,
  caseResultKey,
  formatUf,
  formulaReference,
  modeButton,
  shortName,
  ufClass,
  type CapacityCaseResultLike,
  type CapacityRunLike,
  type CapacityVisualFieldLike,
} from "@/components/capacity/capacityFormat";
import {
  useCapacityResultsStore,
  WORST_CASE_ID,
} from "@/state/capacityResultsStore";
import { useObjectInfoStore } from "@/state/objectInfoStore";
import {
  applyCapacityDefinitionView,
  applyCapacityIsolation,
  applyCapacityIndividualField,
  applyCapacitySelectionHighlight,
  applyCapacityStations,
  applyCapacityVisualField,
  clearCapacityDefinitionView,
  clearCapacityIsolation,
  clearCapacityStations,
  clearCapacityVisualField,
  loadCapacityCaseDetail,
  loadCapacityWorstSummary,
  setFeaWireframeVisible,
} from "@/utils/scene/handlers/load_fea_streaming";

// Per-position marker colours (positions 1/2/3). Keep in sync with
// load_fea_streaming CAPACITY_STATION_COLORS.
const STATION_COLORS = ["#38bdf8", "#fbbf24", "#fb7185"];

const CapacityControls: React.FC = () => {
  const {
    results,
    activeRunId,
    activeCaseId,
    showDefinitions,
    showResults,
    isolateDefinitions,
    showRestWireframe,
    activeMetricId,
    selectedModelId,
    selectedResultId,
    failedOnly,
    loading,
    error,
    caseDetail,
    caseDetailLoading,
    worstCaseIds,
    worstSummary,
    worstSummaryLoading,
    toggleWorstCase,
    setWorstCaseIds,
    setActiveRunId,
    setActiveCaseId,
    setShowDefinitions,
    setShowResults,
    setIsolateDefinitions,
    setShowRestWireframe,
    setActiveMetricId,
    setSelectedModelId,
    setSelectedCapacityResult,
    setFailedOnly,
  } = useCapacityResultsStore();
  const pickedName = useObjectInfoStore((s) => s.name);
  const pickedFaceIndex = useObjectInfoStore((s) => s.faceIndex);
  const pickedFileName = useObjectInfoStore((s) => s.fileName);
  const lastHandledPickKeyRef = useRef<string | null>(null);
  const [showInputs, setShowInputs] = useState(false);
  const [showResultsPanel, setShowResultsPanel] = useState(false);
  const [showStations, setShowStations] = useState(false);
  const [individualUf, setIndividualUf] = useState(false);

  const run = useMemo(() => {
    if (!results?.runs?.length) return null;
    return results.runs.find((r) => r.id === activeRunId) ?? results.runs[0];
  }, [results, activeRunId]);

  // v6: the active case's verbose rows are lazy-loaded into caseDetail; legacy
  // (<=v5) sidecars inline them on the run, so fall back to that.
  const activeRows = useMemo(() => {
    if (!run || !activeCaseId) return [];
    return (
      caseDetail[activeCaseId] ??
      run.case_results.filter((row) => row.case_id === activeCaseId)
    );
  }, [run, activeCaseId, caseDetail]);

  // Fetch the active case's detail on demand (v6 per-case files).
  useEffect(() => {
    if (!run?.case_detail || !activeCaseId) return;
    if (activeCaseId === WORST_CASE_ID) return; // not a real case file
    if (caseDetail[activeCaseId] || caseDetailLoading[activeCaseId]) return;
    void loadCapacityCaseDetail(activeCaseId);
  }, [run, activeCaseId, caseDetail, caseDetailLoading]);

  const isWorst = activeCaseId === WORST_CASE_ID;

  // Load the compact worst summary when the worst view is first opened.
  useEffect(() => {
    if (!isWorst || !run?.worst_summary_url) return;
    if (worstSummary || worstSummaryLoading) return;
    void loadCapacityWorstSummary();
  }, [isWorst, run, worstSummary, worstSummaryLoading]);

  // Worst over the selected case subset: per (model, stiffener), the max UF and
  // the case it came from. Shaped like a normal row so the table reuses it.
  const worstRows = useMemo(() => {
    if (!run || !isWorst || !worstSummary) return [];
    const selected = worstCaseIds;
    const best = new Map<string, CapacityCaseResultLike & { worstCaseLabel: string }>();
    for (const caseId of selected) {
      const bucket = worstSummary.cases[caseId];
      if (!bucket) continue;
      for (const lr of bucket.rows) {
        const key = `${lr.m}::${lr.s ?? lr.pg}`;
        const prev = best.get(key);
        if (!prev || (lr.u ?? -Infinity) > (prev.governing_usage ?? -Infinity)) {
          best.set(key, {
            id: key,
            case_id: caseId,
            capacity_model_id: lr.m,
            panel_group: lr.pg,
            stiffener: lr.s ?? undefined,
            governing_usage: lr.u,
            passed: lr.p,
            governing_check: lr.c ?? null,
            governing_clause: lr.cl ?? null,
            checks: [],
            worstCaseLabel: bucket.label ?? caseId,
          });
        }
      }
    }
    let arr = [...best.values()];
    if (failedOnly) arr = arr.filter((r) => !r.passed);
    return arr.sort(
      (a, b) => (b.governing_usage ?? -1) - (a.governing_usage ?? -1),
    );
  }, [run, isWorst, worstSummary, worstCaseIds, failedOnly]);

  const rows = useMemo(() => {
    if (isWorst) return worstRows;
    if (!activeCaseId) return [];
    return activeRows
      .filter((row) => !failedOnly || !row.passed)
      .slice()
      .sort((a, b) => (b.governing_usage ?? -1) - (a.governing_usage ?? -1));
  }, [isWorst, worstRows, activeRows, activeCaseId, failedOnly]);

  const selectedRow = useMemo(() => {
    if (!run || !activeCaseId) return null;
    if (selectedResultId) {
      const resultMatch = activeRows.find(
        (row) => caseResultKey(row) === selectedResultId,
      );
      if (resultMatch) return resultMatch;
    }
    if (!selectedModelId) return null;
    return (
      activeRows
        .filter((row) => row.capacity_model_id === selectedModelId)
        .sort(
          (a, b) => (b.governing_usage ?? -1) - (a.governing_usage ?? -1),
        )[0] ?? null
    );
  }, [run, activeRows, selectedModelId, selectedResultId, activeCaseId]);

  const meshWarningCount = useMemo(() => {
    if (!run) return 0;
    let bad = 0;
    for (const model of run.capacity_models) {
      const stiffeners = (model.stiffeners ?? []) as Array<
        Record<string, unknown>
      >;
      const violates = stiffeners.some(
        (s) => (s.discretization as { ok?: boolean } | undefined)?.ok === false,
      );
      if (violates) bad += 1;
    }
    return bad;
  }, [run]);

  useEffect(() => {
    if (!run) return;
    if (showDefinitions) {
      applyCapacityDefinitionView();
    } else {
      clearCapacityDefinitionView();
    }
    if (showResults && activeCaseId) {
      if (individualUf) {
        // Worst view colours each stiffener strip by its worst-over-selected-cases
        // UF; a specific case uses that case's rows.
        applyCapacityIndividualField(
          buildIndividualUfValues(isWorst ? worstRows : activeRows, run),
        );
      } else {
        void applyCapacityVisualField(activeMetricId, activeCaseId);
      }
    } else {
      clearCapacityVisualField();
    }
    applyCapacitySelectionHighlight();
  }, [
    run,
    activeRows,
    worstRows,
    activeCaseId,
    activeMetricId,
    showDefinitions,
    showResults,
    selectedModelId,
    individualUf,
    selectedRow,
    // Re-run so the colour overlay rebuilds (collapsing / restoring the
    // non-capacity faces) when "Only definitions" toggles.
    isolateDefinitions,
    // Recolour the worst view when the selected case subset changes.
    isWorst,
    worstCaseIds,
  ]);

  useEffect(() => {
    if (!run) return;
    if (isolateDefinitions) {
      applyCapacityIsolation();
    } else {
      clearCapacityIsolation();
    }
  }, [run, isolateDefinitions]);

  // The whole-model wireframe is shown normally, but "Only definitions" hides it
  // (so only the capacity models remain) unless the user opts back in via
  // "Show rest as wireframe".
  useEffect(() => {
    setFeaWireframeVisible(!isolateDefinitions || showRestWireframe);
  }, [run, isolateDefinitions, showRestWireframe]);

  useEffect(() => {
    if (showInputs && showStations && selectedRow) {
      const model = run?.capacity_models.find(
        (m) => m.id === selectedRow.capacity_model_id,
      );
      const stiffeners = (model?.stiffeners ?? []) as Array<
        Record<string, unknown>
      >;
      const stiff = stiffeners.find((s) => s.name === selectedRow.stiffener);
      applyCapacityStations(
        (stiff?.stations as number[][] | undefined) ?? null,
      );
    } else {
      clearCapacityStations();
    }
  }, [run, selectedRow, showInputs, showStations]);

  useEffect(() => {
    if (!selectedRow) {
      setShowResultsPanel(false);
    }
  }, [selectedRow]);

  useEffect(() => {
    const pickKey = pickedName
      ? `${pickedFileName ?? ""}:${pickedName}:${pickedFaceIndex ?? ""}`
      : null;
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
  }, [
    run,
    pickedName,
    pickedFaceIndex,
    pickedFileName,
    showDefinitions,
    setSelectedModelId,
  ]);

  if (!results && !loading && !error) return null;

  return (
    <div className="rounded-sm border border-gray-700 bg-gray-900/95 text-gray-100 text-xs shadow-lg">
      <div className="flex items-center justify-between gap-2 border-b border-gray-700 px-3 py-2">
        <div className="font-semibold tracking-wide">Capacity</div>
        {loading && <div className="text-gray-400">Loading</div>}
        {error && (
          <div className="text-red-300 truncate max-w-[220px]">{error}</div>
        )}
      </div>
      {run?.errors && run.errors.length > 0 && (
        <details className="border-b border-red-600/70 bg-red-950/50 px-3 py-2 text-red-200">
          <summary className="cursor-pointer font-semibold text-red-300">
            {`⛔ ${run.errors.length} capacity check${
              run.errors.length === 1 ? "" : "s"
            } failed with an error — no result is shown for the affected model/case`}
          </summary>
          <ul className="mt-2 space-y-1">
            {run.errors.map((err, i) => (
              <li key={err.id ?? `${err.capacity_model_id}:${err.case_id}:${i}`}>
                <span className="font-mono text-red-100">
                  {err.panel_group}
                  {err.stiffener ? ` / ${err.stiffener}` : ""}
                </span>
                <span className="text-red-400">{` (case ${err.case_id})`}</span>
                <div className="text-red-300/90">{err.message}</div>
              </li>
            ))}
          </ul>
        </details>
      )}
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
                  <option key={r.id} value={r.id}>
                    {r.label ?? r.id}
                  </option>
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
                <option value={WORST_CASE_ID}>
                  Worst (over selected cases)
                </option>
                {run.result_cases.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label ?? c.id}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {meshWarningCount > 0 && (
            <div className="rounded-sm border border-amber-700/60 bg-amber-900/30 px-2 py-1 text-amber-200 text-[11px]">
              ⚠ {meshWarningCount} model(s) under-meshed for SCM2 [6.4.3] —
              fewer than 4 elements along the stiffener (first-order). Resolved
              stresses may be unreliable.
            </div>
          )}

          <div className="flex flex-wrap items-stretch gap-1">
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
            <button
              className={
                modeButton(showRestWireframe) +
                (!isolateDefinitions ? " cursor-not-allowed opacity-50" : "")
              }
              onClick={() => setShowRestWireframe(!showRestWireframe)}
              disabled={!isolateDefinitions}
              title="While 'Only definitions' is on, also draw the rest of the model as a wireframe for context"
            >
              Show rest as wireframe
            </button>
            <button
              className={modeButton(individualUf)}
              onClick={() => setIndividualUf(!individualUf)}
              disabled={!showResults}
              title="Colour each stiffener's tributary strip by its own UF (within-panel variation) instead of the panel maximum — works for a specific case and for the worst-over-selected-cases view"
            >
              Individual UF
            </button>
            <button
              className={
                modeButton(showResultsPanel) +
                (!selectedRow ? " cursor-not-allowed opacity-50" : "")
              }
              onClick={() => setShowResultsPanel((v) => !v)}
              disabled={!selectedRow}
              title={
                selectedRow
                  ? "Open the full per-check results with collapsible detailed calculations"
                  : "Select a capacity result row to open full results"
              }
            >
              Full results
            </button>
          </div>

          <div className="grid grid-cols-[1fr_auto] gap-2 items-end">
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Check</span>
              <select
                className="bg-gray-800 border border-gray-600 rounded-sm px-2 py-1"
                value={activeMetricId}
                onChange={(e) => setActiveMetricId(e.target.value)}
                disabled={!showResults}
              >
                {run.visual_fields.map((field) => (
                  <option key={field.id} value={field.id}>
                    {metricLabel(run, field)}
                  </option>
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

          {isWorst && (
            <div className="border border-gray-700 rounded-sm p-2 space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase text-gray-500">
                  Cases in worst ({worstCaseIds.length}/{run.result_cases.length})
                </span>
                <div className="flex gap-1">
                  <button
                    className="rounded-sm border border-gray-600 px-2 text-[11px] text-gray-300 hover:bg-gray-700"
                    onClick={() =>
                      setWorstCaseIds(run.result_cases.map((c) => c.id))
                    }
                  >
                    All
                  </button>
                  <button
                    className="rounded-sm border border-gray-600 px-2 text-[11px] text-gray-300 hover:bg-gray-700"
                    onClick={() => setWorstCaseIds([])}
                  >
                    None
                  </button>
                </div>
              </div>
              <div className="grid max-h-28 grid-cols-2 gap-x-2 overflow-y-auto">
                {run.result_cases.map((c) => (
                  <label
                    key={c.id}
                    className="inline-flex items-center gap-1 text-[11px] text-gray-300"
                  >
                    <input
                      type="checkbox"
                      checked={worstCaseIds.includes(c.id)}
                      onChange={() => toggleWorstCase(c.id)}
                    />
                    <span className="truncate" title={c.label ?? c.id}>
                      {c.label ?? c.id}
                    </span>
                  </label>
                ))}
              </div>
              {worstSummaryLoading && (
                <div className="text-[11px] text-gray-500">
                  Loading worst summary…
                </div>
              )}
            </div>
          )}

          {showResults && <CapacityLegend />}

          <div className="max-h-64 overflow-y-auto border border-gray-700 rounded-sm">
            <table className="w-full table-fixed text-left">
              <thead className="sticky top-0 bg-gray-800 text-gray-300">
                <tr>
                  <th className="px-2 py-1 w-[40%]">Model</th>
                  <th className="px-2 py-1 w-[18%]">UF</th>
                  <th className="px-2 py-1">Check</th>
                  {isWorst && <th className="px-2 py-1 w-[20%]">Case</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const rowKey = caseResultKey(row);
                  const selected = selectedRow
                    ? caseResultKey(selectedRow) === rowKey
                    : false;
                  const worstCaseLabel = (
                    row as { worstCaseLabel?: string }
                  ).worstCaseLabel;
                  return (
                    <tr
                      key={rowKey}
                      className={
                        "cursor-pointer border-t border-gray-800 hover:bg-gray-800 " +
                        (selected ? "bg-gray-800" : "")
                      }
                      onClick={() => {
                        if (isWorst) {
                          // Drill into the case that produced this worst UF.
                          setActiveCaseId(row.case_id);
                          setSelectedModelId(row.capacity_model_id);
                        } else {
                          setSelectedCapacityResult(
                            row.capacity_model_id,
                            rowKey,
                          );
                        }
                      }}
                    >
                      <td
                        className="px-2 py-1 truncate"
                        title={row.stiffener ?? row.panel_group}
                      >
                        {shortName(row.stiffener ?? row.panel_group)}
                      </td>
                      <td className={ufClass(row.governing_usage)}>
                        {formatUf(row.governing_usage)}
                      </td>
                      <td
                        className="px-2 py-1 truncate"
                        title={row.governing_check ?? ""}
                      >
                        {row.governing_check ?? ""}
                      </td>
                      {isWorst && (
                        <td
                          className="px-2 py-1 truncate text-gray-300"
                          title={worstCaseLabel ?? row.case_id}
                        >
                          {worstCaseLabel ?? row.case_id}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selectedRow && (
            <div className="border border-gray-700 rounded-sm p-2 space-y-1">
              <div className="flex items-center justify-between gap-2">
                <div
                  className="font-semibold truncate"
                  title={selectedRow.capacity_model_id}
                >
                  {shortName(selectedRow.stiffener ?? selectedRow.panel_group)}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    className={modeButton(showInputs) + " text-[11px]"}
                    onClick={() => setShowInputs(!showInputs)}
                    title="Show the structured input used for this check"
                  >
                    Input
                  </button>
                  <button
                    className={modeButton(showResultsPanel) + " text-[11px]"}
                    onClick={() => setShowResultsPanel((v) => !v)}
                    title="Open the full per-check results with collapsible detailed calculations"
                  >
                    Full results
                  </button>
                </div>
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
                  <div
                    key={check.id}
                    className="grid grid-cols-[1fr_auto] gap-x-2 text-gray-300"
                  >
                    <span className="truncate" title={check.label}>
                      {check.label}
                    </span>
                    <span className={ufClass(check.usage)}>
                      {formatUf(check.usage)}
                    </span>
                    <span
                      className="col-span-2 text-[10px] text-gray-500 truncate"
                      title={formulaReference(check)}
                    >
                      {formulaReference(check)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {showInputs &&
            selectedRow &&
            createPortal(
              <FloatingInputPanel
                run={run}
                row={selectedRow}
                rightOffsetPx={
                  showResultsPanel
                    ? CAPACITY_INPUT_RIGHT_WITH_RESULTS_PX
                    : CAPACITY_FLOATING_PANEL_RIGHT_PX
                }
                showStations={showStations}
                onToggleStations={() => setShowStations((v) => !v)}
                onClose={() => {
                  setShowInputs(false);
                  setShowStations(false);
                }}
              />,
              document.body,
            )}

          {showResultsPanel &&
            selectedRow &&
            createPortal(
              <CapacityResultsPanel
                run={run}
                row={selectedRow}
                onClose={() => setShowResultsPanel(false)}
              />,
              document.body,
            )}
        </div>
      )}
    </div>
  );
};

const FloatingInputPanel: React.FC<{
  run: CapacityRunLike;
  row: CapacityCaseResultLike;
  rightOffsetPx: number;
  showStations: boolean;
  onToggleStations: () => void;
  onClose: () => void;
}> = ({ run, row, rightOffsetPx, showStations, onToggleStations, onClose }) => (
  <div
    className="fixed top-16 z-[1000] flex max-h-[80vh] w-72 flex-col rounded-sm border border-gray-700 bg-gray-900/95 text-gray-100 text-xs shadow-lg"
    style={{ right: rightOffsetPx }}
  >
    <div className="border-b border-gray-700 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="font-semibold">Input</div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            className={modeButton(false) + " text-[11px]"}
            onClick={() => downloadUiCase(run, row)}
            title="Export these inputs as a case file you can import into the aibel_dnv_rp_c201_ui app"
          >
            Export
          </button>
          <button
            className={modeButton(showStations) + " text-[11px]"}
            onClick={onToggleStations}
            title="Mark positions 1/2/3 on the model in the 3D view"
          >
            Points
          </button>
          <button
            className="text-gray-400 hover:text-gray-100"
            onClick={onClose}
            title="Close"
          >
            ✕
          </button>
        </div>
      </div>
      <PanelSubtitle run={run} row={row} />
    </div>
    <div className="overflow-y-auto p-3">
      <CapacityInputDetails run={run} row={row} />
    </div>
  </div>
);

/** Two-line "Case / Model" subtitle shared by the Input and Results sidecars. */
const PanelSubtitle: React.FC<{
  run: CapacityRunLike;
  row: CapacityCaseResultLike;
}> = ({ run, row }) => {
  const caseLabel = caseLabelForRow(run, row);
  const modelName = shortName(row.stiffener ?? row.panel_group);
  return (
    <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-[11px] text-gray-400">
      <span className="text-gray-500">Case</span>
      <span className="truncate" title={caseLabel}>
        {caseLabel}
      </span>
      <span className="text-gray-500">Model</span>
      <span className="truncate" title={row.capacity_model_id}>
        {modelName}
      </span>
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

interface InputField {
  symbol?: string;
  label: string;
  value: number | string | null;
  unit?: string;
  pos?: number; // 1/2/3 → colour-coded to the 3D station marker
  ref?: string; // DNV-RP-C201 equation/clause tag, e.g. "(6.17)"
}

interface InputGroup {
  title: string;
  fields: InputField[];
}

const CapacityInputDetails: React.FC<{
  run: CapacityRunLike;
  row: CapacityCaseResultLike;
}> = ({ run, row }) => {
  const groups = useMemo(() => buildInputGroups(run, row), [run, row]);
  return (
    <div className="space-y-2">
      {groups.map((g) => (
        <div key={g.title}>
          <div className="text-[11px] font-semibold text-gray-300">
            {g.title}
          </div>
          <div className="grid grid-cols-[auto_1fr_auto] gap-x-2 gap-y-0.5">
            {g.fields.map((f, i) => (
              <React.Fragment key={i}>
                <span className="font-mono text-gray-500 whitespace-nowrap">
                  {f.pos != null && (
                    <span
                      style={{ color: STATION_COLORS[f.pos - 1] ?? "#94a3b8" }}
                    >
                      ●{" "}
                    </span>
                  )}
                  {f.symbol ?? ""}
                </span>
                <span className="text-gray-400 truncate" title={f.label}>
                  {f.label}
                  {f.ref && <span className="text-gray-600"> {f.ref}</span>}
                </span>
                <span className="font-mono text-right text-gray-100 whitespace-nowrap">
                  {fmtInputField(f)}
                </span>
              </React.Fragment>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

/** Per-element UF values for the "individual UF" view: each stiffener's own line
 *  + tributary plate strip carries that stiffener's UF (max where strips overlap),
 *  so within a panel you see each stiffener's UF rather than the panel maximum. */
function buildIndividualUfValues(
  rows: CapacityCaseResultLike[],
  run: CapacityRunLike,
): Array<{ element_id: number; value: number | null }> {
  const byElement = new Map<number, number>();
  const modelById = new Map(run.capacity_models.map((m) => [m.id, m]));
  const stiffMaps = new Map<string, Map<string, Record<string, unknown>>>();
  for (const r of rows) {
    const model = modelById.get(r.capacity_model_id);
    if (!model) continue;
    let byName = stiffMaps.get(model.id);
    if (!byName) {
      const stiffeners = (model.stiffeners ?? []) as Array<
        Record<string, unknown>
      >;
      byName = new Map(stiffeners.map((s) => [String(s.name), s]));
      stiffMaps.set(model.id, byName);
    }
    const stiff = byName.get(String(r.stiffener));
    if (!stiff) continue;
    const uf = r.governing_usage ?? 0;
    const ids = [
      ...((stiff.element_ids as number[] | undefined) ?? []),
      ...((stiff.tributary_plate_ids as number[] | undefined) ?? []),
    ];
    for (const id of ids) {
      const prev = byElement.get(id);
      if (prev == null || uf > prev) byElement.set(id, uf);
    }
  }
  return [...byElement].map(([element_id, value]) => ({ element_id, value }));
}

function asNum(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

function scaled(v: unknown, factor: number): number | null {
  const n = asNum(v);
  return n == null ? null : n * factor;
}

function fmtInputField(f: InputField): string {
  if (typeof f.value === "string") return f.value;
  if (f.value == null || !Number.isFinite(f.value)) return "-";
  const v = f.value;
  const a = Math.abs(v);
  const s =
    a >= 100
      ? v.toFixed(1)
      : a >= 1 || a === 0
        ? v.toFixed(2)
        : v.toPrecision(3);
  return f.unit && f.unit !== "-" ? `${s} ${f.unit}` : s;
}

/** Map a capacity-model section to the UI's stiffener_type choice. */
function stiffenerTypeForExport(section: Record<string, unknown>): string {
  const bf = asNum(section.flange_width);
  if (bf == null || bf <= 0) return "flatbar";
  // HP/bulb profiles are one-sided — the engine maps them to the angle type.
  if (/^hp|bulb/i.test(String(section.name ?? ""))) return "angle";
  return "tee";
}

/** Build the aibel_dnv_rp_c201_ui ``fe_stiffened`` value map from a result row.
 *  Units match that check's fields (mm / MPa / kNm); stresses come from the same
 *  resolved vectors / loads the Input panel displays. */
function buildUiCaseValues(
  run: CapacityRunLike,
  row: CapacityCaseResultLike,
): Record<string, number | string | boolean> {
  const model = run.capacity_models.find((m) => m.id === row.capacity_model_id);
  const plate = (model?.plates?.[0] ?? {}) as Record<string, unknown>;
  const stiffeners = (model?.stiffeners ?? []) as Array<Record<string, unknown>>;
  const stiff =
    stiffeners.find((s) => s.name === row.stiffener) ?? stiffeners[0] ?? {};
  const section = (stiff.section ?? {}) as Record<string, unknown>;
  const mat = (stiff.material ?? plate.material ?? {}) as Record<string, unknown>;
  const loads = (row.loads ?? {}) as Record<string, unknown>;
  const vec = (row.resolved_vectors ?? {}) as Record<string, unknown>;
  const sigmaX = (vec.AverageLongitudinalMembraneStresses ?? []) as unknown[];
  const num = (v: unknown, factor: number, fallback = 0): number => {
    const n = scaled(v, factor);
    return n == null ? fallback : n;
  };
  // Uniform sigma_x is stored as a single station; fan it out to all three.
  const sx1 = num(sigmaX[0], 1e-6);
  const sx2 = sigmaX[1] != null ? num(sigmaX[1], 1e-6) : sx1;
  const sx3 = sigmaX[2] != null ? num(sigmaX[2], 1e-6) : sx1;
  return {
    model: "general",
    continuous: stiff.continuous !== false,
    z_star: 0,
    fy: num(mat.fy, 1e-6, 355),
    E: num(mat.E, 1e-6, 210000),
    gamma_M: num(mat.gamma_m, 1, 1.15),
    stiffener_type: stiffenerTypeForExport(section),
    hw: num(section.height, 1e3),
    tw: num(section.web_thickness, 1e3),
    bf: num(section.flange_width, 1e3),
    tf: num(section.flange_thickness, 1e3),
    s: num(plate.width, 1e3),
    t: num(plate.thickness, 1e3),
    span: num(stiff.span ?? plate.length, 1e3),
    sigma_x_1: sx1,
    sigma_x_2: sx2,
    sigma_x_3: sx3,
    tau_1: num(loads.tau_1, 1e-6),
    tau_2: num(loads.tau_2, 1e-6),
    tau_3: num(loads.tau_3, 1e-6),
    sigma_y1: num(loads.sigma_y1, 1e-6),
    sigma_y2: num(loads.sigma_y2, 1e-6),
    sigma_y3: num(loads.sigma_y3, 1e-6),
    M_1: num(loads.M_1, 1e-3),
    M_2: num(loads.M_2, 1e-3),
    M_3: num(loads.M_3, 1e-3),
    lateral_pressure: num(loads.p_Sd, 1e-6),
  };
}

function slugForFile(value: string): string {
  return (
    (value || "case").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
  );
}

/** Download the current input as an aibel_dnv_rp_c201_ui case file. The schema
 *  (``aibel-dnv-c201-ui/case@1`` + ``check_id``/``values``) is exactly what that
 *  app's "Import JSON…" expects. */
function downloadUiCase(run: CapacityRunLike, row: CapacityCaseResultLike): void {
  const name = `${shortName(row.stiffener ?? row.panel_group)} ${caseLabelForRow(
    run,
    row,
  )}`.trim();
  const payload = {
    schema: "aibel-dnv-c201-ui/case@1",
    name,
    check_id: "fe_stiffened",
    source: "adapy-capacity-viewer",
    capacity_model_id: row.capacity_model_id,
    case_id: row.case_id,
    values: buildUiCaseValues(run, row),
    saved_at: new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slugForFile(name)}.case.json`;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function buildInputGroups(
  run: CapacityRunLike,
  row: CapacityCaseResultLike,
): InputGroup[] {
  const model = run.capacity_models.find((m) => m.id === row.capacity_model_id);
  const plate = (model?.plates?.[0] ?? {}) as Record<string, unknown>;
  const stiffeners = (model?.stiffeners ?? []) as Array<
    Record<string, unknown>
  >;
  const stiff =
    stiffeners.find((s) => s.name === row.stiffener) ?? stiffeners[0] ?? {};
  const section = (stiff.section ?? {}) as Record<string, unknown>;
  const disc = (stiff.discretization ?? {}) as Record<string, unknown>;
  const mat = (stiff.material ?? plate.material ?? {}) as Record<
    string,
    unknown
  >;
  const loads = (row.loads ?? {}) as Record<string, unknown>;
  const rv = (row.resolved_variables ?? {}) as Record<string, unknown>;
  const vec = (row.resolved_vectors ?? {}) as Record<string, unknown>;
  const sigmaX = (vec.AverageLongitudinalMembraneStresses ?? []) as unknown[];
  const f = (
    symbol: string,
    label: string,
    value: number | string | null,
    unit?: string,
    pos?: number,
    ref?: string,
  ): InputField => ({ symbol, label, value, unit, pos, ref });
  return [
    {
      title: "Geometry",
      fields: [
        f("t", "Plate thickness", scaled(plate.thickness, 1e3), "mm"),
        f("s", "Stiffener spacing", scaled(plate.width, 1e3), "mm"),
        f("l", "Span", scaled(stiff.span ?? plate.length, 1e3), "mm"),
        f("z_w", "Eccentricity", scaled(stiff.eccentricity, 1e3), "mm"),
      ],
    },
    {
      title: "Stiffener section",
      fields: [
        f("", "Profile", (section.name as string) ?? "—"),
        f("h_w", "Web height", scaled(section.height, 1e3), "mm"),
        f("t_w", "Web thickness", scaled(section.web_thickness, 1e3), "mm"),
        f("b_f", "Flange width", scaled(section.flange_width, 1e3), "mm"),
        f(
          "t_f",
          "Flange thickness",
          scaled(section.flange_thickness, 1e3),
          "mm",
        ),
      ],
    },
    {
      title: "Material",
      fields: [
        f("f_y", "Yield strength", scaled(mat.fy, 1e-6), "MPa"),
        f("E", "Young's modulus", scaled(mat.E, 1e-6), "MPa"),
        f("ν", "Poisson", asNum(mat.poisson), "-"),
        f("γ_M", "Material factor", asNum(mat.gamma_m), "-"),
      ],
    },
    {
      title: "Axial membrane stress",
      fields: [
        f("σ_x1", "Position 1", scaled(sigmaX[0], 1e-6), "MPa", 1, "[5.3.2]"),
        f("σ_x2", "Position 2", scaled(sigmaX[1], 1e-6), "MPa", 2),
        f("σ_x3", "Position 3", scaled(sigmaX[2], 1e-6), "MPa", 3),
      ],
    },
    {
      title: "Transverse stress",
      fields: [
        f("σ_y1", "Position 1", scaled(loads.sigma_y1, 1e-6), "MPa", 1, "[5.3.4]"),
        f("σ_y2", "Position 2", scaled(loads.sigma_y2, 1e-6), "MPa", 2),
        f("σ_y3", "Position 3", scaled(loads.sigma_y3, 1e-6), "MPa", 3),
      ],
    },
    {
      title: "Shear",
      fields: [
        f("τ_1", "Position 1", scaled(loads.tau_1, 1e-6), "MPa", 1, "[5.3.5]"),
        f("τ_2", "Position 2", scaled(loads.tau_2, 1e-6), "MPa", 2),
        f("τ_3", "Position 3", scaled(loads.tau_3, 1e-6), "MPa", 3),
      ],
    },
    {
      title: "Moment",
      fields: [
        f("M_1", "Position 1", scaled(loads.M_1, 1e-3), "kN·m", 1, "[5.3.3]"),
        f("M_2", "Position 2", scaled(loads.M_2, 1e-3), "kN·m", 2),
        f("M_3", "Position 3", scaled(loads.M_3, 1e-3), "kN·m", 3),
      ],
    },
    {
      title: "Lateral load",
      fields: [
        f("p_Sd", "Lateral pressure", scaled(loads.p_Sd, 1e-3), "kPa"),
      ],
    },
    {
      title: "Resolved design (Section 6)",
      fields: [
        f(
          "σ_ySd",
          "Transverse design stress",
          scaled(rv.SigmaYSd, 1e-6),
          "MPa",
          undefined,
          "(6.17)",
        ),
        f(
          "τ_Sd",
          "Shear design stress",
          scaled(rv.TauSd, 1e-6),
          "MPa",
          undefined,
          "(6.16)",
        ),
        f(
          "N_Sd",
          "Axial design force",
          scaled(rv.Nsd, 1e-3),
          "kN",
          undefined,
          "(6.2)",
        ),
      ],
    },
    {
      title: "Options",
      fields: [
        f("", "Continuous", stiff.continuous === false ? "no" : "yes"),
        f("", "Tension field", (loads.tension_field as string) ?? "none"),
      ],
    },
    {
      title: "Modelling [6.4.3]",
      fields: [
        f("", "Elements along stiffener", asNum(disc.elements_along)),
        f(
          "",
          "Element order",
          disc.element_order === 2 ? "2nd order" : "1st order",
        ),
        f("", "Min. required", asNum(disc.min_required)),
        f(
          "",
          "Status",
          disc.ok === false ? "⚠ under-meshed" : "OK",
          undefined,
          undefined,
          "[6.4.3]",
        ),
      ],
    },
  ];
}

function metricLabel(
  run: CapacityRunLike,
  field: CapacityVisualFieldLike,
): string {
  const ref = metricReference(run, field);
  return ref ? `${field.label} ${ref}` : field.label;
}

function metricReference(
  run: CapacityRunLike,
  field: CapacityVisualFieldLike,
): string {
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
  const stillSelected = candidates.find(
    (model) => model.id === selectedModelId,
  );
  if (stillSelected) return stillSelected;

  const scoreByModel = activeMetricScores(run, activeCaseId, activeMetricId);
  return (
    candidates.slice().sort((a, b) => {
      const scoreDiff =
        (scoreByModel.get(b.id) ?? -Infinity) -
        (scoreByModel.get(a.id) ?? -Infinity);
      if (scoreDiff !== 0) return scoreDiff;
      return (
        (a.element_ids.all?.length ?? Number.MAX_SAFE_INTEGER) -
        (b.element_ids.all?.length ?? Number.MAX_SAFE_INTEGER)
      );
    })[0] ?? null
  );
}

function activeMetricScores(
  run: CapacityRunLike,
  activeCaseId: string | null,
  activeMetricId: string,
): Map<string, number> {
  const out = new Map<string, number>();
  const caseId =
    activeCaseId ?? run.result_cases[0]?.id ?? run.field_case_steps?.[0];
  if (!caseId) return out;

  // Legacy (<=v5) inline json field path.
  const field = run.visual_fields.find((f) => f.id === activeMetricId);
  const fieldCase = field?.cases?.find((c) => c.case_id === caseId);
  if (fieldCase) {
    for (const value of fieldCase.values) {
      if (
        !value.capacity_model_id ||
        value.value == null ||
        !isFinite(value.value)
      )
        continue;
      const previous = out.get(value.capacity_model_id);
      if (previous == null || value.value > previous)
        out.set(value.capacity_model_id, value.value);
    }
    return out;
  }

  // v6: per-model score from the active case's loaded detail rows. This is a
  // tie-breaker for picking (which model an element belongs to), so missing
  // (not-yet-loaded) detail just falls back to element-count ordering.
  const store = useCapacityResultsStore.getState();
  const rows =
    store.caseDetail[caseId] ??
    run.case_results.filter((r) => r.case_id === caseId);
  const checkId =
    activeMetricId.startsWith("capacity.uf.") &&
    activeMetricId !== "capacity.uf.governing"
      ? activeMetricId.slice("capacity.uf.".length)
      : null;
  for (const row of rows) {
    const score = checkId
      ? row.checks?.find((c) => c.id === checkId)?.usage ?? null
      : row.governing_usage;
    if (score == null || !isFinite(score)) continue;
    const previous = out.get(row.capacity_model_id);
    if (previous == null || score > previous) {
      out.set(row.capacity_model_id, score);
    }
  }
  return out;
}

function elementIdFromName(name: string): number | null {
  const match = /^E(\d+)$/.exec(name.trim());
  if (!match) return null;
  const parsed = Number.parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : null;
}

export default CapacityControls;
