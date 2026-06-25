import { create } from "zustand";

import type { CapacityManifest } from "@/services/viewerApi";

export interface CapacitySource {
  sourceName: string;
  resultsUrl: string;
}

export interface CapacityCheckResult {
  id: string;
  label: string;
  clause?: string;
  equations?: string[];
  usage: number | null;
  passed: boolean;
  advisory?: boolean;
  demand?: number | null;
  resistance?: number | null;
  unit?: string;
  intermediates?: Record<string, number | string | null>;
  warnings?: string[];
  assumptions?: string[];
}

export interface CapacityCheckCatalogEntry {
  id: string;
  label?: string;
  clause?: string;
  equations?: string[];
  advisory?: boolean;
}

export interface CapacityCaseResult {
  id?: string;
  case_id: string;
  case_label?: string;
  capacity_model_id: string;
  panel_group: string;
  stiffener?: string;
  governing_usage: number | null;
  passed: boolean;
  governing_check?: string | null;
  governing_clause?: string | null;
  checks: CapacityCheckResult[];
  loads?: Record<string, unknown>;
  resolved_variables?: Record<string, unknown>;
  resolved_vectors?: Record<string, unknown>;
  notes?: string[];
}

export interface CapacityModel {
  id: string;
  panel_group: string;
  stiffener?: string;
  type: string;
  element_ids: {
    plates?: number[];
    stiffeners?: number[];
    all?: number[];
  };
  plates?: Array<Record<string, unknown>>;
  stiffeners?: Array<Record<string, unknown>>;
  geometry?: Record<string, unknown>;
}

export interface CapacityVisualFieldValue {
  element_id: number;
  value: number | null;
  capacity_model_id?: string;
}

export interface CapacityVisualFieldCase {
  case_id: string;
  values: CapacityVisualFieldValue[];
}

export interface CapacityVisualField {
  id: string;
  label: string;
  check_id?: string | null;
  clause?: string | null;
  equations?: string[];
  support: string;
  kind: string;
  storage: "json" | "afel" | string;
  range?: [number, number];
  /** Legacy ``storage: "json"`` only — inline per-(case, element) values. v6
   *  ``storage: "afel"`` fields omit this and carry the blob pointer below. */
  cases?: CapacityVisualFieldCase[];
  /** v6 AFEL colour blob (``storage: "afel"``). The element axis and case-step
   *  axis are shared across all fields and live on the run
   *  (``element_axis`` / ``field_case_steps``). One blob holds every case
   *  (step); the viewer Range-fetches a single (field, case) step. */
  blob_url?: string;
  header_bytes?: number;
  stride_bytes?: number;
  dtype?: string;
  byte_order?: "little" | "big" | string;
}

export interface CapacityCaseDetailPointer {
  strategy: string;
  /** Filename template with a ``{case}`` placeholder, manifest-relative. */
  url_template: string;
}

/** Synthetic "case" id for the worst-over-selected-cases view. */
export const WORST_CASE_ID = "__worst__";

/** One compact row in the worst-over-cases summary (no heavy check detail). */
export interface CapacityWorstRow {
  /** Unique per (case, model, stiffener). */
  k: string;
  /** capacity_model_id. */
  m: string;
  /** stiffener name. */
  s?: string | null;
  /** panel_group. */
  pg: string;
  /** governing usage factor. */
  u: number | null;
  /** passed. */
  p: boolean;
  /** governing check id. */
  c?: string | null;
  /** governing clause. */
  cl?: string | null;
}

export interface CapacityWorstSummary {
  format: string;
  version: number;
  cases: Record<string, { label?: string; rows: CapacityWorstRow[] }>;
}

/** One check that raised and was skipped (so it never reached the results). The
 *  capacity model is still listed in ``capacity_models`` with its geometry, but
 *  has no usage factor for this case. */
export interface CapacityRunError {
  id?: string;
  case_id: string;
  capacity_model_id: string;
  panel_group: string;
  stiffener?: string;
  /** Exception text, usually prefixed with the offending clause, e.g.
   *  "[6.21] Square root of negative value ...". */
  message: string;
}

export interface CapacityRun {
  id: string;
  label?: string;
  standard?: string;
  scope?: string;
  group?: string | null;
  result_cases: Array<{ id: string; label?: string }>;
  capacity_models: CapacityModel[];
  check_catalog?: CapacityCheckCatalogEntry[];
  /** v6: empty in the spine; per-case rows are lazy-loaded into the store's
   *  ``caseDetail`` map. Legacy sidecars (<=v5) inline the full array here. */
  case_results: CapacityCaseResult[];
  /** v7 (additive): checks that raised and were skipped. Absent/empty on a clean
   *  run and on older sidecars. */
  errors?: CapacityRunError[];
  visual_fields: CapacityVisualField[];
  /** v6: shared element axis for the AFEL colour blobs (payload-row order). */
  element_axis?: number[];
  /** v6: shared case-step axis for the AFEL colour blobs (step order). */
  field_case_steps?: string[];
  /** v6: pointer to the per-case detail files. */
  case_detail?: CapacityCaseDetailPointer;
  /** v6: manifest-relative URL of the compact worst-over-cases summary. */
  worst_summary_url?: string;
}

export interface CapacityResults {
  format: string;
  version: number;
  source?: Record<string, unknown>;
  runs: CapacityRun[];
}

export interface CapacityResultsState {
  manifest: CapacityManifest | null;
  source: CapacitySource | null;
  results: CapacityResults | null;
  activeRunId: string | null;
  activeCaseId: string | null;
  activeMode: "definition" | "results";
  showDefinitions: boolean;
  showResults: boolean;
  isolateDefinitions: boolean;
  /** With "Only definitions" (isolateDefinitions) on, optionally still draw the
   *  rest of the model as a wireframe for context. Default on — the capacity
   *  models stand out while the rest stays visible as a faint wireframe. */
  showRestWireframe: boolean;
  activeMetricId: string;
  selectedModelId: string | null;
  selectedResultId: string | null;
  failedOnly: boolean;
  loading: boolean;
  error: string | null;
  /** v6 lazy per-case detail: ``case_id -> rows``, fetched on demand when a
   *  case becomes active. Legacy (<=v5) sidecars never populate this; controls
   *  fall back to the inline ``run.case_results``. */
  caseDetail: Record<string, CapacityCaseResult[]>;
  caseDetailLoading: Record<string, boolean>;
  /** Worst-over-cases: which result cases are included (default all). */
  worstCaseIds: string[];
  worstSummary: CapacityWorstSummary | null;
  worstSummaryLoading: boolean;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setCapacityData: (
    manifest: CapacityManifest,
    source: CapacitySource,
    results: CapacityResults,
  ) => void;
  setCaseDetail: (caseId: string, rows: CapacityCaseResult[]) => void;
  setCaseDetailLoading: (caseId: string, loading: boolean) => void;
  setWorstCaseIds: (caseIds: string[]) => void;
  toggleWorstCase: (caseId: string) => void;
  setWorstSummary: (summary: CapacityWorstSummary | null) => void;
  setWorstSummaryLoading: (loading: boolean) => void;
  clear: () => void;
  setActiveRunId: (runId: string | null) => void;
  setActiveCaseId: (caseId: string | null) => void;
  setActiveMode: (mode: "definition" | "results") => void;
  setShowDefinitions: (showDefinitions: boolean) => void;
  setShowResults: (showResults: boolean) => void;
  setIsolateDefinitions: (isolateDefinitions: boolean) => void;
  setShowRestWireframe: (showRestWireframe: boolean) => void;
  setActiveMetricId: (metricId: string) => void;
  setSelectedModelId: (modelId: string | null) => void;
  setSelectedCapacityResult: (
    modelId: string | null,
    resultId: string | null,
  ) => void;
  setFailedOnly: (failedOnly: boolean) => void;
}

const DEFAULT_METRIC = "capacity.uf.governing";

export const useCapacityResultsStore = create<CapacityResultsState>((set) => ({
  manifest: null,
  source: null,
  results: null,
  activeRunId: null,
  activeCaseId: null,
  activeMode: "results",
  showDefinitions: true,
  showResults: true,
  isolateDefinitions: true,
  showRestWireframe: true,
  activeMetricId: DEFAULT_METRIC,
  selectedModelId: null,
  selectedResultId: null,
  failedOnly: false,
  loading: false,
  error: null,
  caseDetail: {},
  caseDetailLoading: {},
  worstCaseIds: [],
  worstSummary: null,
  worstSummaryLoading: false,
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  setCapacityData: (manifest, source, results) => {
    const run = pickRun(results, manifest.default_run_id);
    const caseId =
      run?.result_cases?.[0]?.id ?? run?.case_results?.[0]?.case_id ?? null;
    set({
      manifest,
      source,
      results,
      activeRunId: run?.id ?? null,
      activeCaseId: caseId,
      showDefinitions: true,
      showResults: true,
      isolateDefinitions: true,
      showRestWireframe: true,
      activeMetricId: DEFAULT_METRIC,
      selectedModelId: null,
      selectedResultId: null,
      caseDetail: {},
      caseDetailLoading: {},
      worstCaseIds: run?.result_cases?.map((c) => c.id) ?? [],
      worstSummary: null,
      worstSummaryLoading: false,
      loading: false,
      error: null,
    });
  },
  setCaseDetail: (caseId, rows) =>
    set((state) => ({
      caseDetail: { ...state.caseDetail, [caseId]: rows },
      caseDetailLoading: { ...state.caseDetailLoading, [caseId]: false },
    })),
  setCaseDetailLoading: (caseId, loading) =>
    set((state) => ({
      caseDetailLoading: { ...state.caseDetailLoading, [caseId]: loading },
    })),
  setWorstCaseIds: (worstCaseIds) => set({ worstCaseIds }),
  toggleWorstCase: (caseId) =>
    set((state) => {
      const has = state.worstCaseIds.includes(caseId);
      return {
        worstCaseIds: has
          ? state.worstCaseIds.filter((id) => id !== caseId)
          : [...state.worstCaseIds, caseId],
      };
    }),
  setWorstSummary: (worstSummary) => set({ worstSummary, worstSummaryLoading: false }),
  setWorstSummaryLoading: (worstSummaryLoading) => set({ worstSummaryLoading }),
  clear: () =>
    set({
      manifest: null,
      source: null,
      results: null,
      activeRunId: null,
      activeCaseId: null,
      activeMode: "results",
      showDefinitions: true,
      showResults: true,
      isolateDefinitions: true,
      showRestWireframe: true,
      activeMetricId: DEFAULT_METRIC,
      selectedModelId: null,
      selectedResultId: null,
      failedOnly: false,
      caseDetail: {},
      caseDetailLoading: {},
      worstCaseIds: [],
      worstSummary: null,
      worstSummaryLoading: false,
      loading: false,
      error: null,
    }),
  setActiveRunId: (activeRunId) =>
    set({ activeRunId, selectedModelId: null, selectedResultId: null }),
  setActiveCaseId: (activeCaseId) =>
    set({ activeCaseId, selectedModelId: null, selectedResultId: null }),
  setActiveMode: (activeMode) => set({ activeMode }),
  setShowDefinitions: (showDefinitions) => set({ showDefinitions }),
  setShowResults: (showResults) => set({ showResults }),
  setIsolateDefinitions: (isolateDefinitions) => set({ isolateDefinitions }),
  setShowRestWireframe: (showRestWireframe) => set({ showRestWireframe }),
  setActiveMetricId: (activeMetricId) => set({ activeMetricId }),
  setSelectedModelId: (selectedModelId) =>
    set({ selectedModelId, selectedResultId: null }),
  setSelectedCapacityResult: (selectedModelId, selectedResultId) =>
    set({ selectedModelId, selectedResultId }),
  setFailedOnly: (failedOnly) => set({ failedOnly }),
}));

function pickRun(
  results: CapacityResults,
  preferredId?: string,
): CapacityRun | null {
  if (!results.runs?.length) return null;
  if (preferredId) {
    const preferred = results.runs.find((run) => run.id === preferredId);
    if (preferred) return preferred;
  }
  return results.runs[0] ?? null;
}
