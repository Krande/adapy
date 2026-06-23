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
  cases: CapacityVisualFieldCase[];
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
  case_results: CapacityCaseResult[];
  visual_fields: CapacityVisualField[];
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
  activeMetricId: string;
  selectedModelId: string | null;
  selectedResultId: string | null;
  failedOnly: boolean;
  loading: boolean;
  error: string | null;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setCapacityData: (
    manifest: CapacityManifest,
    source: CapacitySource,
    results: CapacityResults,
  ) => void;
  clear: () => void;
  setActiveRunId: (runId: string | null) => void;
  setActiveCaseId: (caseId: string | null) => void;
  setActiveMode: (mode: "definition" | "results") => void;
  setShowDefinitions: (showDefinitions: boolean) => void;
  setShowResults: (showResults: boolean) => void;
  setIsolateDefinitions: (isolateDefinitions: boolean) => void;
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
  isolateDefinitions: false,
  activeMetricId: DEFAULT_METRIC,
  selectedModelId: null,
  selectedResultId: null,
  failedOnly: false,
  loading: false,
  error: null,
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
      isolateDefinitions: false,
      activeMetricId: DEFAULT_METRIC,
      selectedModelId: null,
      selectedResultId: null,
      loading: false,
      error: null,
    });
  },
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
      isolateDefinitions: false,
      activeMetricId: DEFAULT_METRIC,
      selectedModelId: null,
      selectedResultId: null,
      failedOnly: false,
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
