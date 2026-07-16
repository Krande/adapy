import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import CapacityResultsPanel from "@/components/capacity/CapacityResultsPanel";
import { buildCodecheckCasePayload } from "@/services/codecheckCase";
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
  applyCapacityGirderLineUf,
  applyCapacityIsolation,
  applyCapacityIndividualField,
  applyCapacitySelectionHighlight,
  applyCapacityStations,
  clearCapacityDefinitionView,
  clearCapacityIsolation,
  clearCapacityStations,
  clearCapacityVisualField,
  loadCapacityCaseDetail,
  loadCapacityProvenance,
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
  const pickedClick = useObjectInfoStore((s) => s.clickCoordinate);
  const lastHandledPickKeyRef = useRef<string | null>(null);
  const lastHandledClickRef = useRef<object | null>(null);
  const [showInputs, setShowInputs] = useState(false);
  const [showResultsPanel, setShowResultsPanel] = useState(false);
  const [showStations, setShowStations] = useState(false);

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
        const errorWins = !!lr.e && !prev?.error;
        const sameStatusAndHigherUf =
          !!lr.e === !!prev?.error &&
          (lr.u ?? -Infinity) > (prev?.governing_usage ?? -Infinity);
        if (!prev || errorWins || sameStatusAndHigherUf) {
          best.set(key, {
            id: key,
            case_id: caseId,
            capacity_model_id: lr.m,
            panel_group: lr.pg,
            stiffener: lr.s ?? undefined,
            governing_usage: lr.u,
            passed: lr.p,
            governing_check: lr.e ? "error" : lr.c ?? null,
            governing_clause: lr.cl ?? null,
            error: lr.e ?? null,
            checks: [],
            worstCaseLabel: bucket.label ?? caseId,
          });
        }
      }
    }
    let arr = [...best.values()];
    if (failedOnly) arr = arr.filter((r) => !r.passed);
    return arr.sort((a, b) => capacityRowScore(b) - capacityRowScore(a));
  }, [run, isWorst, worstSummary, worstCaseIds, failedOnly]);

  const rows = useMemo(() => {
    if (isWorst) return worstRows;
    if (!activeCaseId) return [];
    return activeRows
      .filter((row) => !failedOnly || !row.passed)
      .slice()
      .sort((a, b) => capacityRowScore(b) - capacityRowScore(a));
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
        .sort((a, b) => capacityRowScore(b) - capacityRowScore(a))[0] ?? null
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
      // Always colour per stiffener strip ("individual UF"): each stiffener's
      // own line + tributary plate carries that stiffener's UF for the active
      // metric. The worst view colours by the worst-over-selected-cases UF; a
      // specific case uses that case's rows. Girder models colour the girder
      // line itself; their tributary plates light up only when selected.
      const rows = isWorst ? worstRows : activeRows;
      applyCapacityIndividualField(
        buildIndividualUfValues(rows, run, activeMetricId),
      );
      applyCapacityGirderLineUf(buildGirderUfMap(rows, run, activeMetricId));
    } else {
      clearCapacityVisualField();
      applyCapacityGirderLineUf(null);
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
      lastHandledClickRef.current = null;
      return;
    }
    // Each 3D click stores a fresh clickCoordinate object, so a repeat click on
    // the same face re-enters here (to drill into the individual strip below);
    // effect re-runs from unrelated deps (same click object) stay deduped.
    if (
      lastHandledPickKeyRef.current === pickKey &&
      lastHandledClickRef.current === pickedClick
    )
      return;
    lastHandledPickKeyRef.current = pickKey;
    lastHandledClickRef.current = pickedClick;
    if (!run || !pickedName || !showDefinitions) return;
    const elementId = elementIdFromName(pickedName);
    if (elementId == null) return;
    const {
      activeCaseId: currentCaseId,
      activeMetricId: currentMetricId,
      selectedModelId: currentSelectedModelId,
      selectedResultId: currentSelectedResultId,
    } = useCapacityResultsStore.getState();
    const match = pickCapacityModelForElement(
      run,
      elementId,
      currentCaseId,
      currentMetricId,
      currentSelectedModelId,
    );
    if (!match) return;
    if (match.id !== currentSelectedModelId) {
      setSelectedModelId(match.id);
      return;
    }
    // Click inside the already-selected model: select the individual
    // stiffener/strip under the cursor so the Input/Results/Points panels
    // (and the amber strip highlight) follow that specific capacity model.
    const rowKey = pickResultRowForElement(
      run,
      match,
      elementId,
      currentCaseId,
      currentMetricId,
    );
    if (rowKey && rowKey !== currentSelectedResultId) {
      setSelectedCapacityResult(match.id, rowKey);
    }
  }, [
    run,
    pickedName,
    pickedFaceIndex,
    pickedFileName,
    pickedClick,
    showDefinitions,
    setSelectedModelId,
    setSelectedCapacityResult,
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
      {/* Notices — errors and warnings sit together at the top, presented the
          same way (full-width collapsible banners), error (red) above
          warning (amber). */}
      {run?.errors && run.errors.length > 0 && (
        <details className="border-b border-red-600/70 bg-red-950/50 px-3 py-2 text-red-200">
          <summary className="cursor-pointer font-semibold text-red-300">
            {`⛔ ${run.errors.length} capacity check${
              run.errors.length === 1 ? "" : "s"
            } failed with an error — affected rows are treated as failures`}
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
      {meshWarningCount > 0 && (
        <details className="border-b border-amber-700/60 bg-amber-950/40 px-3 py-2 text-amber-200">
          <summary className="cursor-pointer font-semibold text-amber-300">
            {`⚠ ${meshWarningCount} model(s) under-meshed for SCM2 [6.4.3]`}
          </summary>
          <div className="mt-2 text-amber-300/90">
            Fewer than 4 elements along the stiffener (first-order). Resolved
            stresses may be unreliable.
          </div>
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
                        className={
                          "px-2 py-1 truncate " +
                          (row.error ? "font-semibold text-red-400" : "")
                        }
                        title={row.error ?? row.governing_check ?? ""}
                      >
                        {row.error ? "error" : row.governing_check ?? ""}
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
                <div
                  className={
                    selectedRow.error ? "font-semibold text-red-400" : ""
                  }
                >
                  {selectedRow.error
                    ? "ERROR"
                    : selectedRow.passed
                      ? "OK"
                      : "FAIL"}
                </div>
              </div>
              {selectedRow.error && (
                <div className="rounded-sm border border-red-700/60 bg-red-950/40 px-2 py-1 text-red-200">
                  {selectedRow.error}
                </div>
              )}
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
            title="Export these inputs as a case file you can import into the codecheck.ui app"
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
    {/* Genie discrete UF bands (0.2/0.4/0.6/0.8/1.0) over a 0..1.2 bar. */}
    <div className="h-2 rounded-sm capacity-uf-gradient" />
    <div className="relative h-3 text-[10px] text-gray-400">
      <span className="absolute left-0">0</span>
      <span className="absolute left-[16.6667%] -translate-x-1/2">0.2</span>
      <span className="absolute left-[33.3333%] -translate-x-1/2">0.4</span>
      <span className="absolute left-1/2 -translate-x-1/2">0.6</span>
      <span className="absolute left-[66.6667%] -translate-x-1/2">0.8</span>
      <span className="absolute left-[83.3333%] -translate-x-1/2">1.0</span>
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
  provenance?: InputProvenance;
  provenanceKey?: string;
  provenanceUrl?: string;
}

interface InputGroup {
  title: string;
  fields: InputField[];
}

interface InputProvenance {
  label?: string;
  calculation?: string;
  formula?: string;
  terms?: ProvenanceTerm[];
  source_sets?: ProvenanceSourceSet[];
  sources?: ProvenanceSource[];
}

interface ProvenanceTerm {
  label?: string;
  value?: number | string | null;
  unit?: string;
}

interface ProvenanceSourceSet {
  label?: string;
  source_count?: number;
  element_ids?: number[];
  sources?: ProvenanceSource[];
  truncated_source_count?: number;
}

interface ProvenanceSource {
  element_id?: number;
  node_ids?: number[];
  result_points?: number[];
  force_position?: number;
  along_m?: number;
  value?: number | string | null;
  raw_value?: number | string | null;
  unit?: string;
  calculation?: string;
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
              <InputFieldRow key={`${caseResultKey(row)}:${g.title}:${i}`} field={f} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

const InputFieldRow: React.FC<{ field: InputField }> = ({ field: f }) => {
  const [open, setOpen] = useState(false);
  const [loadedProvenance, setLoadedProvenance] = useState<
    InputProvenance | undefined
  >(undefined);
  const [loadingProvenance, setLoadingProvenance] = useState(false);
  const [provenanceError, setProvenanceError] = useState<string | null>(null);
  const provenance = f.provenance ?? loadedProvenance;
  const canFetchProvenance = !!f.provenanceUrl && !!f.provenanceKey;
  const hasProvenance = !!provenance || canFetchProvenance;

  useEffect(() => {
    setLoadedProvenance(undefined);
    setLoadingProvenance(false);
    setProvenanceError(null);
    setOpen(false);
  }, [f.provenanceUrl, f.provenanceKey]);

  const toggleProvenance = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (provenance || !f.provenanceUrl || !f.provenanceKey) return;
    setLoadingProvenance(true);
    setProvenanceError(null);
    try {
      const payload = await loadCapacityProvenance(f.provenanceUrl);
      const item = provenanceFor(payload, f.provenanceKey);
      if (item) {
        setLoadedProvenance(item);
      } else {
        setProvenanceError("No provenance recorded for this value.");
      }
    } catch (err) {
      setProvenanceError("Could not load provenance.");
      // eslint-disable-next-line no-console
      console.warn(`[capacity] failed to load provenance ${f.provenanceUrl}:`, err);
    } finally {
      setLoadingProvenance(false);
    }
  };

  return (
    <>
      <span className="font-mono text-gray-500 whitespace-nowrap">
        {f.pos != null && (
          <span style={{ color: STATION_COLORS[f.pos - 1] ?? "#94a3b8" }}>
            {"\u25cf "}
          </span>
        )}
        {f.symbol ?? ""}
      </span>
      <span className="text-gray-400 truncate" title={f.label}>
        {f.label}
        {f.ref && <span className="text-gray-600"> {f.ref}</span>}
      </span>
      <span className="font-mono text-right text-gray-100 whitespace-nowrap">
        {hasProvenance ? (
          <button
            type="button"
            className="text-right underline decoration-dotted decoration-gray-500 underline-offset-2 hover:text-white focus:outline-none focus:ring-1 focus:ring-sky-500/80"
            aria-expanded={open}
            onClick={() => void toggleProvenance()}
          >
            {fmtInputField(f)}
          </button>
        ) : (
          fmtInputField(f)
        )}
      </span>
      {open && hasProvenance && (
        <div className="col-span-3 mb-1 border-l border-sky-500/40 pl-2 text-[10px] text-gray-400">
          {loadingProvenance ? (
            <div>Loading provenance...</div>
          ) : provenance ? (
            <InputProvenanceDetails provenance={provenance} />
          ) : (
            <div>{provenanceError ?? "No provenance recorded for this value."}</div>
          )}
        </div>
      )}
    </>
  );
};

const InputProvenanceDetails: React.FC<{ provenance: InputProvenance }> = ({
  provenance,
}) => {
  const sets = provenance.source_sets ?? [];
  const directSources = provenance.sources ?? [];
  return (
    <div className="max-h-56 space-y-1 overflow-auto pr-1">
      {provenance.calculation && <div>{provenance.calculation}</div>}
      {provenance.formula && (
        <div className="font-mono text-gray-300">{provenance.formula}</div>
      )}
      {provenance.terms?.length ? (
        <div className="grid grid-cols-[1fr_auto] gap-x-2">
          {provenance.terms.map((term, i) => (
            <React.Fragment key={i}>
              <span className="text-gray-500">{term.label ?? ""}</span>
              <span className="font-mono text-gray-300">
                {fmtProvenanceValue(term.value, term.unit)}
              </span>
            </React.Fragment>
          ))}
        </div>
      ) : null}
      {sets.map((set, i) => (
        <ProvenanceSourceSetView key={i} sourceSet={set} />
      ))}
      {directSources.length ? (
        <ProvenanceSourceSetView
          sourceSet={{
            label: "sources",
            source_count: directSources.length,
            sources: directSources,
          }}
        />
      ) : null}
    </div>
  );
};

const ProvenanceSourceSetView: React.FC<{ sourceSet: ProvenanceSourceSet }> = ({
  sourceSet,
}) => {
  const elements = sourceSet.element_ids ?? [];
  const sources = sourceSet.sources ?? [];
  return (
    <div className="space-y-0.5">
      <div className="text-gray-300">
        {sourceSet.label ?? "sources"}
        {sourceSet.source_count != null && (
          <span className="text-gray-500"> ({sourceSet.source_count})</span>
        )}
      </div>
      {elements.length ? (
        <div>
          <span className="text-gray-500">Elements </span>
          <span className="font-mono text-gray-300">
            {compactNumberList(elements)}
          </span>
        </div>
      ) : null}
      {sources.map((source, i) => (
        <div key={i} className="font-mono text-gray-400">
          {formatProvenanceSource(source)}
        </div>
      ))}
      {sourceSet.truncated_source_count ? (
        <div className="text-gray-500">
          {sourceSet.truncated_source_count} more source rows
        </div>
      ) : null}
    </div>
  );
};

/** Per-element UF values for the per-stiffener colour view: each stiffener's own
 *  line + tributary plate strip carries that stiffener's UF (max where strips
 *  overlap), so within a panel you see each stiffener's UF rather than the panel
 *  maximum. A specific check metric uses that check's usage from the row; rows
 *  without per-check usages (the worst-summary rows) fall back to the governing
 *  UF. */
function capacityRowScore(row: CapacityCaseResultLike): number {
  // A missing engineering result is more severe than any finite utilization.
  return row.error ? Number.MAX_VALUE : (row.governing_usage ?? -1);
}

function buildIndividualUfValues(
  rows: CapacityCaseResultLike[],
  run: CapacityRunLike,
  activeMetricId: string,
): Array<{ element_id: number; value: number | null }> {
  const checkId = metricCheckId(activeMetricId);
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
    const uf = r.error ? 1.01 : rowMetricUf(r, checkId);
    if (uf == null) continue; // this check does not apply to the row
    // Girder tributary plates use the same opaque UF overlay as stiffened-panel
    // capacity models. Overlapping strips retain the governing value via the
    // max merge below, so the view stays deterministic without looking faded.
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

/** Specific check id for a metric, or null for the governing-UF metric. */
function metricCheckId(activeMetricId: string): string | null {
  return activeMetricId.startsWith("capacity.uf.") &&
    activeMetricId !== "capacity.uf.governing"
    ? activeMetricId.slice("capacity.uf.".length)
    : null;
}

/** The row's UF for the active metric (governing, or a specific check). */
function rowMetricUf(
  r: CapacityCaseResultLike,
  checkId: string | null,
): number | null {
  if (checkId && r.checks?.length) {
    return r.checks.find((c) => c.id === checkId)?.usage ?? null;
  }
  return r.governing_usage ?? 0;
}

/** Per-girder-model UF for colouring the girder lines in the 3D view. */
function buildGirderUfMap(
  rows: CapacityCaseResultLike[],
  run: CapacityRunLike,
  activeMetricId: string,
): Map<string, number | null> {
  const checkId = metricCheckId(activeMetricId);
  const girderIds = new Set(
    run.capacity_models
      .filter((m) => (m as { type?: string }).type === "girder")
      .map((m) => m.id),
  );
  const out = new Map<string, number | null>();
  if (girderIds.size === 0) return out;
  for (const r of rows) {
    if (!girderIds.has(r.capacity_model_id)) continue;
    const uf = r.error ? 1.01 : rowMetricUf(r, checkId);
    if (uf == null) continue;
    const prev = out.get(r.capacity_model_id);
    if (prev == null || uf > prev) out.set(r.capacity_model_id, uf);
  }
  return out;
}

function asNum(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

function scaled(v: unknown, factor: number): number | null {
  const n = asNum(v);
  return n == null ? null : n * factor;
}

function provenanceFor(
  provenance: Record<string, unknown>,
  key: string,
): InputProvenance | undefined {
  const value = provenance[key];
  return value && typeof value === "object" ? (value as InputProvenance) : undefined;
}

/** Round a value to the precision the input sidecar shows (see fmtInputField):
 *  1 decimal for |v|>=100, 2 decimals for |v|>=1, else 3 significant figures.
 *  Used so the exported numbers match what the user reads in the sidebar
 *  (e.g. 180.0 rather than 180.00000715255737). */
function displayRound(v: number): number {
  if (!Number.isFinite(v)) return v;
  const a = Math.abs(v);
  if (a >= 100) return Number(v.toFixed(1));
  if (a >= 1 || a === 0) return Number(v.toFixed(2));
  return Number(v.toPrecision(3));
}

function fmtInputField(f: InputField): string {
  if (typeof f.value === "string") return f.value;
  if (f.value == null || !Number.isFinite(f.value)) return "-";
  const v = f.value;
  const a = Math.abs(v);
  const s =
    a >= 1e6
      ? v.toExponential(3)
      : a >= 100
        ? v.toFixed(1)
        : a >= 1 || a === 0
          ? v.toFixed(2)
          : v.toPrecision(3);
  return f.unit && f.unit !== "-" ? `${s} ${f.unit}` : s;
}

function fmtProvenanceValue(value: unknown, unit?: string): string {
  if (typeof value === "string") return unit && unit !== "-" ? `${value} ${unit}` : value;
  const n = asNum(value);
  if (n == null) return "-";
  const a = Math.abs(n);
  const s =
    a >= 1e6 || (a > 0 && a < 1e-3)
      ? n.toExponential(3)
      : a >= 100
        ? n.toFixed(2)
        : a >= 1 || a === 0
          ? n.toFixed(4)
          : n.toPrecision(4);
  return unit && unit !== "-" ? `${s} ${unit}` : s;
}

function compactNumberList(values: number[], limit = 18): string {
  const shown = values.slice(0, limit).join(", ");
  return values.length > limit ? `${shown}, +${values.length - limit}` : shown;
}

function formatProvenanceSource(source: ProvenanceSource): string {
  const parts: string[] = [];
  if (source.element_id != null) parts.push(`el ${source.element_id}`);
  if (source.node_ids?.length) parts.push(`nodes ${compactNumberList(source.node_ids, 8)}`);
  if (source.result_points?.length) {
    parts.push(`rp ${compactNumberList(source.result_points, 8)}`);
  }
  if (source.force_position != null) parts.push(`pos ${source.force_position}`);
  if (source.along_m != null) parts.push(`x=${fmtProvenanceValue(source.along_m, "m")}`);
  if (source.value != null) parts.push(`value ${fmtProvenanceValue(source.value, source.unit)}`);
  if (source.raw_value != null) parts.push(`raw ${fmtProvenanceValue(source.raw_value, source.unit)}`);
  return parts.join(" | ");
}

/** Map a capacity-model section to the UI's stiffener_type choice. */
function stiffenerTypeForExport(section: Record<string, unknown>): string {
  const bf = asNum(section.flange_width);
  if (bf == null || bf <= 0) return "flatbar";
  // HP/bulb profiles are one-sided — the engine maps them to the angle type.
  if (/^hp|bulb/i.test(String(section.name ?? ""))) return "angle";
  return "tee";
}

/** Geometry/material a stiffener result was checked with, in mm / MPa.
 *  ``profileName`` / ``eccentricityMm`` are display-only (not part of what the
 *  engine consumes). */
interface ResolvedGeometry {
  span: number | null;
  s: number | null;
  t: number | null;
  hw: number | null;
  tw: number | null;
  bf: number | null;
  tf: number | null;
  stiffenerType: string;
  profileName: string;
  eccentricityMm: number | null;
  fy: number | null;
  E: number | null;
  nu: number | null;
  gammaM: number | null;
  continuous: boolean;
  optimizeZStar: boolean;
}

/** Resolve the geometry/material for a result row, preferring the v8
 *  ``check_inputs`` (the exact values the engine consumed) over the display
 *  ``capacity_model`` dict. The two can differ — notably the stiffener span,
 *  which drives the transverse plate resistance sigma_y,R (eq. 4.6) — so using
 *  ``check_inputs`` is what makes the Input panel and the Export reproduce the
 *  engine result. Falls back to the display dict for pre-v8 bundles. */
function resolveGeometry(
  run: CapacityRunLike,
  row: CapacityCaseResultLike,
): ResolvedGeometry {
  const model = run.capacity_models.find((m) => m.id === row.capacity_model_id);
  const plate = (model?.plates?.[0] ?? {}) as Record<string, unknown>;
  const stiffeners = (model?.stiffeners ?? []) as Array<Record<string, unknown>>;
  const stiff =
    stiffeners.find((s) => s.name === row.stiffener) ?? stiffeners[0] ?? {};
  const section = (stiff.section ?? {}) as Record<string, unknown>;
  const mat = (stiff.material ?? plate.material ?? {}) as Record<string, unknown>;
  const profileName = String(section.name ?? "—");
  const eccentricityMm = scaled(stiff.eccentricity, 1e3);
  const ci = row.check_inputs;
  if (ci) {
    return {
      span: asNum(ci.span_mm),
      s: asNum(ci.plate?.s_mm),
      t: asNum(ci.plate?.t_mm),
      hw: asNum(ci.stiffener?.hw_mm),
      tw: asNum(ci.stiffener?.tw_mm),
      bf: asNum(ci.stiffener?.bf_mm),
      tf: asNum(ci.stiffener?.tf_mm),
      stiffenerType: ci.stiffener?.type ?? stiffenerTypeForExport(section),
      profileName,
      eccentricityMm,
      fy: asNum(ci.material?.fy_mpa),
      E: asNum(ci.material?.E_mpa),
      nu: asNum(mat.poisson),
      gammaM: asNum(ci.material?.gamma_m),
      continuous: ci.continuous !== false,
      optimizeZStar: ci.optimize_z_star === true,
    };
  }
  return {
    span: scaled(stiff.span ?? plate.length, 1e3),
    s: scaled(plate.width, 1e3),
    t: scaled(plate.thickness, 1e3),
    hw: scaled(section.height, 1e3),
    tw: scaled(section.web_thickness, 1e3),
    bf: scaled(section.flange_width, 1e3),
    tf: scaled(section.flange_thickness, 1e3),
    stiffenerType: stiffenerTypeForExport(section),
    profileName,
    eccentricityMm,
    fy: scaled(mat.fy, 1e-6),
    E: scaled(mat.E, 1e-6),
    nu: asNum(mat.poisson),
    gammaM: asNum(mat.gamma_m),
    continuous: stiff.continuous !== false,
    optimizeZStar: false,
  };
}

/** Build the codecheck.ui ``fe_stiffened`` value map from a result row.
 *  Units match that check's fields (mm / MPa / kNm). Geometry/material come from
 *  the as-checked ``check_inputs`` (so an imported case reproduces the engine);
 *  stresses come from the same resolved vectors / loads the Input panel shows. */
function buildUiCaseValues(
  run: CapacityRunLike,
  row: CapacityCaseResultLike,
): Record<string, number | string | boolean> {
  const g = resolveGeometry(run, row);
  const loads = (row.loads ?? {}) as Record<string, unknown>;
  const vec = (row.resolved_vectors ?? {}) as Record<string, unknown>;
  const sigmaX = (vec.AverageLongitudinalMembraneStresses ?? []) as unknown[];
  // Round to the sidebar's display precision so the exported numbers match
  // what the user reads (180.0, not 180.00000715255737).
  const num = (v: unknown, factor: number, fallback = 0): number => {
    const n = scaled(v, factor);
    return displayRound(n == null ? fallback : n);
  };
  const dr = (v: number | null, fallback: number): number =>
    displayRound(v == null ? fallback : v);
  // Uniform sigma_x is stored as a single station; fan it out to all three.
  const sx1 = num(sigmaX[0], 1e-6);
  const sx2 = sigmaX[1] != null ? num(sigmaX[1], 1e-6) : sx1;
  const sx3 = sigmaX[2] != null ? num(sigmaX[2], 1e-6) : sx1;
  return {
    model: "general",
    continuous: g.continuous,
    z_star: 0,
    optimize_z_star: g.optimizeZStar,
    fy: dr(g.fy, 355),
    E: dr(g.E, 210000),
    gamma_M: dr(g.gammaM, 1.15),
    stiffener_type: g.stiffenerType,
    hw: dr(g.hw, 0),
    tw: dr(g.tw, 0),
    bf: dr(g.bf, 0),
    tf: dr(g.tf, 0),
    s: dr(g.s, 0),
    t: dr(g.t, 0),
    span: dr(g.span, 0),
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

/** Girder run ``check_inputs`` payload (see
 *  girder_capacity_check._build_girder_check_inputs — mm / MPa / mm² / mm⁴). */
interface GirderCheckInputs {
  girder?: {
    hw_mm?: number;
    tw_mm?: number;
    bf_mm?: number;
    tf_mm?: number;
    section?: string;
  };
  bay?: {
    LG_mm?: number;
    l_mm?: number;
    l1_mm?: number;
    l2_mm?: number;
    s_mm?: number;
    continuous?: boolean;
  };
  plate?: { s_mm?: number; t_mm?: number };
  stiffener?: {
    As_mm2?: number;
    Is_mm4?: number;
    section?: string;
    continuous_through_girder?: boolean;
    // Representative stiffener profile (sidecar v11+; bulbs already idealized
    // as angles) — needed by the UI's Stipla DNV-G export.
    type?: string;
    hw_mm?: number;
    tw_mm?: number;
    bf_mm?: number;
    tf_mm?: number;
  };
  material?: { fy_mpa?: number; E_mpa?: number; gamma_m?: number };
  method?: string;
  welded?: boolean;
}

function girderCheckInputs(row: CapacityCaseResultLike): GirderCheckInputs {
  return (row.check_inputs ?? {}) as unknown as GirderCheckInputs;
}

/** Build the codecheck.ui ``fe_girder`` value map from a girder result
 *  row. Geometry comes from the as-checked ``check_inputs``; the loads invert
 *  ``GirderLoads.from_membrane_stress`` (eq. 5.2): the UI takes girder-direction
 *  membrane stresses, so sigma_y,i = N_Gi / (A_G + l·t) with A_G = hw·tw + bf·tf
 *  — the same section the UI rebuilds, so the imported case reproduces the
 *  engine loads. */
function buildUiGirderValues(
  row: CapacityCaseResultLike,
): Record<string, number | string | boolean> {
  const ci = girderCheckInputs(row);
  const loads = (row.loads ?? {}) as Record<string, unknown>;
  const vec = (row.resolved_vectors ?? {}) as Record<string, unknown>;
  const sigmaXVec = (vec.AverageStiffenerDirectionMembraneStresses ?? []) as unknown[];
  const dr = (v: number | null, fallback = 0): number =>
    displayRound(v == null ? fallback : v);
  const num = (v: unknown, factor: number): number =>
    displayRound(scaled(v, factor) ?? 0);
  // Optional stiffener-profile dimension: blank when the sidecar lacks it.
  const stDim = (v: unknown): number | string => {
    const n = asNum(v);
    return n == null ? "" : displayRound(n);
  };
  const hw = asNum(ci.girder?.hw_mm) ?? 0;
  const tw = asNum(ci.girder?.tw_mm) ?? 0;
  const bf = asNum(ci.girder?.bf_mm) ?? 0;
  const tf = asNum(ci.girder?.tf_mm) ?? 0;
  const lSpan = asNum(ci.bay?.l_mm) ?? 0;
  const l1Span = asNum(ci.bay?.l1_mm) ?? lSpan;
  const l2Span = asNum(ci.bay?.l2_mm) ?? lSpan;
  const t = asNum(ci.plate?.t_mm) ?? 0;
  const areaMm2 = hw * tw + bf * tf + lSpan * t;
  // N [N] / A [mm²] = sigma [MPa] exactly.
  const sigmaY = (nG: unknown): number => {
    const n = asNum(nG);
    return displayRound(areaMm2 > 0 && n != null ? n / areaMm2 : 0);
  };
  const sigmaX = num(loads.sigma_x_Sd, 1e-6);
  const sigmaXAt = (index: number): number =>
    sigmaXVec[index] != null ? num(sigmaXVec[index], 1e-6) : sigmaX;
  return {
    method: ci.method ?? "GCM3",
    effective_width_method: "auto",
    z_star: 0,
    k_moment_reduction: 1,
    welded: ci.welded !== false,
    fy: dr(asNum(ci.material?.fy_mpa), 355),
    E: dr(asNum(ci.material?.E_mpa), 210000),
    gamma_M: dr(asNum(ci.material?.gamma_m), 1.15),
    hw: dr(hw),
    tw: dr(tw),
    bf: dr(bf),
    tf: dr(tf),
    flange_type: "symmetric",
    s: dr(asNum(ci.plate?.s_mm)),
    t: dr(t),
    As: dr(asNum(ci.stiffener?.As_mm2)),
    Is: dr(asNum(ci.stiffener?.Is_mm4)),
    // Stiffener profile (bulbs already idealized as angles by the sidecar):
    // the UI's Stipla DNV-G export needs the profile, not just A_s/I_s. Blank
    // on older sidecars — the explicit A_s/I_s above still govern the check.
    stiffener_type: ci.stiffener?.type ?? "tee",
    st_hw: stDim(ci.stiffener?.hw_mm),
    st_tw: stDim(ci.stiffener?.tw_mm),
    st_bf: stDim(ci.stiffener?.bf_mm),
    st_tf: stDim(ci.stiffener?.tf_mm),
    continuous_through_girder: ci.stiffener?.continuous_through_girder !== false,
    LG: dr(asNum(ci.bay?.LG_mm)),
    l_span: dr(lSpan),
    l1_span: dr(l1Span),
    l2_span: dr(l2Span),
    // The sidecar run leaves the optional lengths at the engine defaults
    // (L_Gk = L_G, L_GT = L_G, no panel length): export explicit blanks so the
    // UI form does not substitute its own suggested defaults on import.
    LGk: "",
    LGT: "",
    Lp: "",
    continuous: ci.bay?.continuous !== false,
    sigma_y_1: sigmaY(loads.N_G1),
    sigma_y_2: sigmaY(loads.N_G2),
    sigma_y_3: sigmaY(loads.N_G3),
    M_1: num(loads.M_G1, 1e-3),
    M_2: num(loads.M_G2, 1e-3),
    M_3: num(loads.M_G3, 1e-3),
    tau_1: num(loads.tau_1, 1e-6),
    tau_2: num(loads.tau_2, 1e-6),
    tau_3: num(loads.tau_3, 1e-6),
    sigma_x: sigmaX,
    sigma_x_1: sigmaXAt(0),
    sigma_x_2: sigmaXAt(1),
    sigma_x_3: sigmaXAt(2),
    shear_force: num(loads.V_Sd, 1e-3),
    lateral_pressure: num(loads.p_Sd, 1e-6),
    p_dir: num(loads.p_dir, 1e-6),
  };
}

function slugForFile(value: string): string {
  return (
    (value || "case").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
  );
}

/** Download the current input as a codecheck.ui case file. The schema
 *  (``codecheck/case@1`` + ``standard_id``/``check_id``/``values``) is what that
 *  app's "Import JSON…" expects. Girder rows export the Section-7 ``fe_girder``
 *  check; panel rows the ``fe_stiffened`` check. */
function downloadUiCase(run: CapacityRunLike, row: CapacityCaseResultLike): void {
  const isGirder =
    run.capacity_models.find((m) => m.id === row.capacity_model_id)?.type ===
    "girder";
  const name = `${shortName(row.stiffener ?? row.panel_group)} ${caseLabelForRow(
    run,
    row,
  )}`.trim();
  const payload = buildCodecheckCasePayload({
    name,
    check_id: isGirder ? "fe_girder" : "fe_stiffened",
    capacity_model_id: row.capacity_model_id,
    case_id: row.case_id,
    values: isGirder
      ? buildUiGirderValues(row)
      : buildUiCaseValues(run, row),
  });
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
  if (model?.type === "girder") return buildGirderInputGroups(row);
  const plate = (model?.plates?.[0] ?? {}) as Record<string, unknown>;
  const stiffeners = (model?.stiffeners ?? []) as Array<
    Record<string, unknown>
  >;
  const stiff =
    stiffeners.find((s) => s.name === row.stiffener) ?? stiffeners[0] ?? {};
  const disc = (stiff.discretization ?? {}) as Record<string, unknown>;
  const loads = (row.loads ?? {}) as Record<string, unknown>;
  const rv = (row.resolved_variables ?? {}) as Record<string, unknown>;
  const vec = (row.resolved_vectors ?? {}) as Record<string, unknown>;
  const provenance = (row.resolved_provenance ?? {}) as Record<string, unknown>;
  const sigmaX = (vec.AverageLongitudinalMembraneStresses ?? []) as unknown[];
  // Show the geometry/material the check actually used (v8 check_inputs), so the
  // sidebar matches the result and the Export.
  const g = resolveGeometry(run, row);
  // v8: DNV-RP-C201-correct Section-6 design values from the engine's own eqs
  // (6.16/6.17/6.2). Fall back to the Genie resolved_variables for pre-v8
  // bundles. (Genie's SigmaYSd is the mid-point value, not eq 6.17 — see the
  // standard — so prefer the engine figures when present.)
  const rd = (row.resolved_design ?? {}) as Record<string, unknown>;
  const sigmaYSd = asNum(rd.sigma_y_Sd_mpa) ?? scaled(rv.SigmaYSd, 1e-6);
  const tauSd = asNum(rd.tau_Sd_mpa) ?? scaled(rv.TauSd, 1e-6);
  const nSd = asNum(rd.N_Sd_kn) ?? scaled(rv.Nsd, 1e-3);
  const f = (
    symbol: string,
    label: string,
    value: number | string | null,
    unit?: string,
    pos?: number,
    ref?: string,
    provenanceKey?: string,
  ): InputField => ({
    symbol,
    label,
    value,
    unit,
    pos,
    ref,
    provenanceKey,
    provenance: provenanceKey ? provenanceFor(provenance, provenanceKey) : undefined,
    provenanceUrl: provenanceKey ? row.provenance_url : undefined,
  });
  return [
    {
      title: "Geometry",
      fields: [
        f("t", "Plate thickness", g.t, "mm"),
        f("s", "Stiffener spacing", g.s, "mm"),
        f("l", "Span", g.span, "mm"),
        f("z_w", "Eccentricity", g.eccentricityMm, "mm"),
      ],
    },
    {
      title: "Stiffener section",
      fields: [
        f("", "Profile", g.profileName),
        f("h_w", "Web height", g.hw, "mm"),
        f("t_w", "Web thickness", g.tw, "mm"),
        f("b_f", "Flange width", g.bf, "mm"),
        f("t_f", "Flange thickness", g.tf, "mm"),
      ],
    },
    {
      title: "Material",
      fields: [
        f("f_y", "Yield strength", g.fy, "MPa"),
        f("E", "Young's modulus", g.E, "MPa"),
        f("ν", "Poisson", g.nu, "-"),
        f("γ_M", "Material factor", g.gammaM, "-"),
      ],
    },
    {
      title: "Axial membrane stress",
      fields: [
        f("σ_x1", "Position 1", scaled(sigmaX[0], 1e-6), "MPa", 1, "[5.3.2]", "sigma_x_1"),
        f("σ_x2", "Position 2", scaled(sigmaX[1], 1e-6), "MPa", 2, undefined, "sigma_x_2"),
        f("σ_x3", "Position 3", scaled(sigmaX[2], 1e-6), "MPa", 3, undefined, "sigma_x_3"),
      ],
    },
    {
      title: "Transverse stress",
      fields: [
        f("σ_y1", "Position 1", scaled(loads.sigma_y1, 1e-6), "MPa", 1, "[5.3.4]", "sigma_y1"),
        f("σ_y2", "Position 2", scaled(loads.sigma_y2, 1e-6), "MPa", 2, undefined, "sigma_y2"),
        f("σ_y3", "Position 3", scaled(loads.sigma_y3, 1e-6), "MPa", 3, undefined, "sigma_y3"),
      ],
    },
    {
      title: "Shear",
      fields: [
        f("τ_1", "Position 1", scaled(loads.tau_1, 1e-6), "MPa", 1, "[5.3.5]", "tau_1"),
        f("τ_2", "Position 2", scaled(loads.tau_2, 1e-6), "MPa", 2, undefined, "tau_2"),
        f("τ_3", "Position 3", scaled(loads.tau_3, 1e-6), "MPa", 3, undefined, "tau_3"),
      ],
    },
    {
      title: "Moment",
      fields: [
        f("M_1", "Position 1", scaled(loads.M_1, 1e-3), "kN·m", 1, "[5.3.3]", "M_1"),
        f("M_2", "Position 2", scaled(loads.M_2, 1e-3), "kN·m", 2, undefined, "M_2"),
        f("M_3", "Position 3", scaled(loads.M_3, 1e-3), "kN·m", 3, undefined, "M_3"),
      ],
    },
    {
      title: "Lateral load",
      fields: [
        f("p_Sd", "Lateral pressure", scaled(loads.p_Sd, 1e-3), "kPa", undefined, undefined, "p_Sd"),
      ],
    },
    {
      title: "Resolved design (Section 6)",
      fields: [
        f("σ_ySd", "Transverse design stress", sigmaYSd, "MPa", undefined, "(6.17)"),
        f("τ_Sd", "Shear design stress", tauSd, "MPa", undefined, "(6.16)"),
        f("N_Sd", "Axial design force", nSd, "kN", undefined, "(6.2)"),
      ],
    },
    {
      title: "Options",
      fields: [
        f("", "Continuous", g.continuous ? "yes" : "no"),
        f("", "Optimize z*", g.optimizeZStar ? "yes" : "no", undefined, undefined, "[6.10.2]"),
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

/** Section-7 girder Input panel: the girder bay/section, the supported
 *  stiffeners and the [7.8] design loads — the girder counterpart of the panel
 *  groups above, built from the girder ``check_inputs`` + ``loads``. */
function buildGirderInputGroups(row: CapacityCaseResultLike): InputGroup[] {
  const ci = girderCheckInputs(row);
  const loads = (row.loads ?? {}) as Record<string, unknown>;
  const vec = (row.resolved_vectors ?? {}) as Record<string, unknown>;
  const provenance = (row.resolved_provenance ?? {}) as Record<string, unknown>;
  const sigmaXVec = (vec.AverageStiffenerDirectionMembraneStresses ?? []) as unknown[];
  const hw = asNum(ci.girder?.hw_mm) ?? 0;
  const tw = asNum(ci.girder?.tw_mm) ?? 0;
  const bf = asNum(ci.girder?.bf_mm) ?? 0;
  const tf = asNum(ci.girder?.tf_mm) ?? 0;
  const lSpan = asNum(ci.bay?.l_mm) ?? 0;
  const l1Span = asNum(ci.bay?.l1_mm) ?? lSpan;
  const l2Span = asNum(ci.bay?.l2_mm) ?? lSpan;
  const t = asNum(ci.plate?.t_mm) ?? 0;
  const areaMm2 = hw * tw + bf * tf + lSpan * t;
  const sigmaY = (nG: unknown): number | null => {
    const n = asNum(nG);
    return areaMm2 > 0 && n != null ? displayRound(n / areaMm2) : null;
  };
  const sigmaX = scaled(loads.sigma_x_Sd, 1e-6);
  const sigmaXAt = (index: number): number | null =>
    sigmaXVec[index] != null ? scaled(sigmaXVec[index], 1e-6) : sigmaX;
  const f = (
    symbol: string,
    label: string,
    value: number | string | null,
    unit?: string,
    pos?: number,
    ref?: string,
    provenanceKey?: string,
  ): InputField => ({
    symbol,
    label,
    value,
    unit,
    pos,
    ref,
    provenanceKey,
    provenance: provenanceKey ? provenanceFor(provenance, provenanceKey) : undefined,
    provenanceUrl: provenanceKey ? row.provenance_url : undefined,
  });
  return [
    {
      title: "Girder bay",
      fields: [
        f("L_G", "Girder span", asNum(ci.bay?.LG_mm), "mm"),
        f("L_1", "Adjacent stiffener span", l1Span, "mm", undefined, "[5.3]"),
        f("L_2", "Adjacent stiffener span", l2Span, "mm", undefined, "[5.3]"),
        f("l", "Effective span", lSpan, "mm", undefined, "[7.4]"),
        f("s", "Stiffener spacing", asNum(ci.plate?.s_mm), "mm"),
        f("t", "Plate thickness", asNum(ci.plate?.t_mm), "mm"),
      ],
    },
    {
      title: "Girder section",
      fields: [
        f("", "Profile", ci.girder?.section || "—"),
        f("h_wG", "Web height", asNum(ci.girder?.hw_mm), "mm"),
        f("t_wG", "Web thickness", asNum(ci.girder?.tw_mm), "mm"),
        f("b_fG", "Flange width", asNum(ci.girder?.bf_mm), "mm"),
        f("t_fG", "Flange thickness", asNum(ci.girder?.tf_mm), "mm"),
      ],
    },
    {
      title: "Supported stiffeners",
      fields: [
        f("", "Profile", ci.stiffener?.section || "—"),
        f("h_w", "Web height", asNum(ci.stiffener?.hw_mm), "mm"),
        f("t_w", "Web thickness", asNum(ci.stiffener?.tw_mm), "mm"),
        f("b_f", "Flange width", asNum(ci.stiffener?.bf_mm), "mm"),
        f("t_f", "Flange thickness", asNum(ci.stiffener?.tf_mm), "mm"),
        f("A_s", "Stiffener area", asNum(ci.stiffener?.As_mm2), "mm²"),
        f("I_s", "Moment of inertia", asNum(ci.stiffener?.Is_mm4), "mm⁴"),
        f(
          "",
          "Continuous through girder",
          ci.stiffener?.continuous_through_girder === false ? "no" : "yes",
        ),
      ],
    },
    {
      title: "Material",
      fields: [
        f("f_y", "Yield strength", asNum(ci.material?.fy_mpa), "MPa"),
        f("E", "Young's modulus", asNum(ci.material?.E_mpa), "MPa"),
        f("γ_M", "Material factor", asNum(ci.material?.gamma_m), "-"),
      ],
    },
    {
      title: "Stiffener-direction stress",
      fields: [
        f("σ_x1", "Position 1", sigmaXAt(0), "MPa", 1, "[7.8.5]", "sigma_x_1"),
        f("σ_x2", "Position 2", sigmaXAt(1), "MPa", 2, undefined, "sigma_x_2"),
        f("σ_x3", "Position 3", sigmaXAt(2), "MPa", 3, undefined, "sigma_x_3"),
      ],
    },
    {
      title: "Girder membrane stress",
      fields: [
        f("σ_y1", "Position 1", sigmaY(loads.N_G1), "MPa", 1, "(5.2)", "sigma_y_1"),
        f("σ_y2", "Position 2", sigmaY(loads.N_G2), "MPa", 2, undefined, "sigma_y_2"),
        f("σ_y3", "Position 3", sigmaY(loads.N_G3), "MPa", 3, undefined, "sigma_y_3"),
      ],
    },
    {
      title: "Girder axial force (compression +)",
      fields: [
        f("N_G1", "Position 1", scaled(loads.N_G1, 1e-3), "kN", 1, "[7.8.2]", "N_G1"),
        f("N_G2", "Position 2", scaled(loads.N_G2, 1e-3), "kN", 2, undefined, "N_G2"),
        f("N_G3", "Position 3", scaled(loads.N_G3, 1e-3), "kN", 3, undefined, "N_G3"),
      ],
    },
    {
      title: "Girder moment (tension in plate flange +)",
      fields: [
        f("M_G1", "Position 1", scaled(loads.M_G1, 1e-3), "kN·m", 1, "(7.42)", "M_G1"),
        f("M_G2", "Position 2", scaled(loads.M_G2, 1e-3), "kN·m", 2, "(7.43)", "M_G2"),
        f("M_G3", "Position 3", scaled(loads.M_G3, 1e-3), "kN·m", 3, "(7.44)", "M_G3"),
      ],
    },
    {
      title: "Shear stress",
      fields: [
        f("τ_1", "Position 1", scaled(loads.tau_1, 1e-6), "MPa", 1, "(7.45)", "tau_1"),
        f("τ_2", "Position 2", scaled(loads.tau_2, 1e-6), "MPa", 2, undefined, "tau_2"),
        f("τ_3", "Position 3", scaled(loads.tau_3, 1e-6), "MPa", 3, undefined, "tau_3"),
      ],
    },
    {
      title: "Other loads",
      fields: [
        f("p_Sd", "Lateral pressure", scaled(loads.p_Sd, 1e-3), "kPa", undefined, undefined, "p_Sd"),
        f("V_Sd", "Web shear force", scaled(loads.V_Sd, 1e-3), "kN", undefined, "(7.68)", "V_Sd"),
      ],
    },
    {
      title: "Method",
      fields: [
        f("", "Girder capacity model", ci.method ?? "GCM3", undefined, undefined, "Table 7-1"),
        f("", "Continuous girder", ci.bay?.continuous === false ? "no" : "yes"),
        f("", "Welded", ci.welded === false ? "no" : "yes"),
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

/** Result-row key for the individual stiffener/strip of ``model`` that owns the
 *  picked element, so a click inside an already-selected panel selects that
 *  specific capacity model (not just the panel's worst). Overlapping strips
 *  resolve to the highest UF for the active metric. The worst view keeps
 *  model-level selection (its rows aggregate over cases). */
function pickResultRowForElement(
  run: CapacityRunLike,
  model: CapacityRunLike["capacity_models"][number],
  elementId: number,
  activeCaseId: string | null,
  activeMetricId: string,
): string | null {
  if (!activeCaseId || activeCaseId === WORST_CASE_ID) return null;
  const stiffeners = (model.stiffeners ?? []) as Array<Record<string, unknown>>;
  const owners = new Set(
    stiffeners
      .filter((s) => {
        const own = (s.element_ids as number[] | undefined) ?? [];
        const trib = (s.tributary_plate_ids as number[] | undefined) ?? [];
        return own.includes(elementId) || trib.includes(elementId);
      })
      .map((s) => String(s.name)),
  );
  if (owners.size === 0) return null;
  const store = useCapacityResultsStore.getState();
  const rows = (
    store.caseDetail[activeCaseId] ??
    run.case_results.filter((r) => r.case_id === activeCaseId)
  ).filter(
    (r) => r.capacity_model_id === model.id && owners.has(String(r.stiffener)),
  );
  if (rows.length === 0) return null;
  const checkId = metricCheckId(activeMetricId);
  const best = rows
    .slice()
    .sort(
      (a, b) => (rowMetricUf(b, checkId) ?? -1) - (rowMetricUf(a, checkId) ?? -1),
    )[0];
  return caseResultKey(best);
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
