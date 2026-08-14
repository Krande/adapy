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
  status?: "OK" | "FAIL" | "ADVISORY" | string;
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
  /** Exception text when the check could not produce an engineering result. */
  error?: string | null;
  checks: CapacityCheckResult[];
  loads?: Record<string, unknown>;
  resolved_variables?: Record<string, unknown>;
  resolved_vectors?: Record<string, unknown>;
  resolved_provenance?: Record<string, unknown>;
  provenance_url?: string;
  /** v13 key selecting this row inside an aggregated per-case provenance file. */
  provenance_key?: string;
  /** v8: the exact geometry/material/options the check consumed (mm/MPa). The
   *  input panel and Export prefer this over the display capacity_model dict so
   *  an exported case reproduces the engine — notably the stiffener span that
   *  drives the transverse plate resistance sigma_y,R (eq. 4.6). */
  check_inputs?: CapacityCheckInputs;
  /** v8: the Section-6 design values the engine derives from the three-position
   *  loads via DNV-RP-C201 eqs (6.16/6.17/6.2). Shown in "Resolved design" so
   *  the figures are standard-correct (not the Genie reference convention). */
  resolved_design?: CapacityResolvedDesign;
  notes?: string[];
}

/** v8 resolved_design payload (see viewer_export._resolved_design_payload). */
export interface CapacityResolvedDesign {
  sigma_y_Sd_mpa?: number | null;
  tau_Sd_mpa?: number | null;
  N_Sd_kn?: number | null;
}

/** v8 check_inputs payload (see capacity_check._build_check_inputs). */
export interface CapacityCheckInputs {
  span_mm?: number;
  plate?: { s_mm?: number; t_mm?: number };
  stiffener?: {
    hw_mm?: number;
    tw_mm?: number;
    bf_mm?: number;
    tf_mm?: number;
    type?: string;
  };
  material?: { fy_mpa?: number; E_mpa?: number; gamma_m?: number };
  continuous?: boolean;
  welded?: boolean;
  model_type?: string;
  optimize_z_star?: boolean;
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
  /** v15, worst_summary only: the run's shared string table. Shard rows carry
   *  indices into this instead of spelling the names out. It lives on the spine
   *  rather than in each shard because the same names recur in every case. */
  strings?: string[];
}

/** Synthetic "case" id for the worst-over-selected-cases view. */
export const WORST_CASE_ID = "__worst__";

/** One run's share of a streaming calculation (capacity.progress.json). */
export interface CapacityCalcRunProgress {
  id: string;
  scope: string;
  label?: string;
  cases_total: number;
  cases_ready: string[];
  complete: boolean;
  elapsed_s?: number;
  /** False for a run that is announced but has not begun — the girder check
   *  only starts once the panel check is done, and showing it as queued beats
   *  having a second bar appear from nowhere. */
  started?: boolean;
  /** How many names this run's shared string table holds. The table only ever
   *  appends, so a count above what the viewer has cached is the signal to
   *  re-read it (see `strings_url`). */
  strings_count?: number;
}

/** One preparation step of a streaming calculation — reading the SIN,
 *  rebuilding capacity models, recovering stress fields. The counts mirror the
 *  bar the same step fills in the terminal; ``total`` is 0 for a step that
 *  cannot report one, and the viewer runs it indeterminate. */
export interface CapacityCalcStep {
  label: string;
  done: boolean;
  elapsed_s: number;
  /** Steps nest (panel-group identification runs geometry steps inside it). */
  depth?: number;
  completed?: number;
  total?: number;
}

/** Published by a `--stream-results` run while the checks are still going, so
 *  the viewer can show results as they land instead of waiting for the lot.
 *  Absent for a normal run, where the bundle is complete before the viewer
 *  opens. Runs finish at very different times — the girder check is several
 *  times quicker than the panel check — so each reports its own state. */
export interface CapacityCalcProgress {
  format?: string;
  complete: boolean;
  phase: string;
  message?: string;
  cases_total: number;
  cases_done: number;
  /** False until the checks have enumerated their work — during the long
   *  start-up (baking, SIN read, model reconstruction) there is no case count
   *  yet, so the bar runs indeterminate rather than showing a bogus 0/0. */
  cases_known?: boolean;
  elapsed_s?: number;
  /** Recent phase transitions, newest last. */
  log?: Array<{ at_s: number; phase: string; message: string }>;
  /** Everything before per-case checking — model reconstruction, stress
   *  recovery, load resolution. Over a minute of the wait before the first
   *  case lands on a large model, so it gets its own row. */
  prep?: {
    /** True when no step is running. Not "every run has started": the girder
     *  check only begins once panels are done, and the old reading left the
     *  viewer animating a finished step list for minutes. */
    complete: boolean;
    active: string | null;
    steps: CapacityCalcStep[];
  };
  runs: CapacityCalcRunProgress[];
  /** Bundle-relative URL of the worst-summary string tables as they stand right
   *  now, keyed by run id. The spine's copy is only rewritten when a run's
   *  first case lands, so mid-run it can be short of what later shards index
   *  into — leaving the odd governing-check cell blank. Read this instead while
   *  a run is in flight. */
  strings_url?: string;
  updated_utc?: string;
}

/** What the progress poller should do after a failed read.
 *
 *  * `retry` — try again; a single failure means nothing.
 *  * `stop-no-run` — no progress file ever appeared: an ordinary finished
 *    bundle, so clear the run status and stop polling.
 *  * `stop-keep` — a run *was* publishing and has gone quiet for a long time:
 *    stop polling but keep the last status on screen.
 *
 *  Separated out because getting it wrong is expensive: treating one torn read
 *  of a file the calculation is rewriting as "no run" cleared the run status
 *  mid-run and, with it, the record of which cases exist — after which the
 *  worst view requested every shard and failed on all of them. */
export function progressPollFailureAction(
  everSeen: boolean,
  consecutiveFailures: number,
  limits: { initialAttempts: number; maxFailures: number },
): "retry" | "stop-no-run" | "stop-keep" {
  if (!everSeen) {
    return consecutiveFailures >= limits.initialAttempts ? "stop-no-run" : "retry";
  }
  return consecutiveFailures >= limits.maxFailures ? "stop-keep" : "retry";
}

/** Whether the spine (capacity.results.json) has to be re-read.
 *
 *  The spine is what the panel's run list, model list and case list come from,
 *  and a streaming run rewrites it as each run's first case lands. This used to
 *  trigger on the *number of runs in the progress file* changing — but both
 *  runs are announced before either starts, so that number is 2 from the first
 *  poll and never changed again. The girder run therefore stayed missing from
 *  the "Capacity check type" dropdown for the whole run, even while it was
 *  publishing results.
 *
 *  The real condition is a run that has published cases but is not in the spine
 *  we hold. */
export function needsSpineReload(
  runs: CapacityCalcRunProgress[],
  knownRunIds: readonly string[],
  hasResults: boolean,
  complete: boolean,
): boolean {
  if (!hasResults || complete) return true;
  const known = new Set(knownRunIds);
  return runs.some((run) => run.cases_ready.length > 0 && !known.has(run.id));
}

/** Which worst-summary string table to decode a run's shards with.
 *
 *  Three sources can disagree mid-run: the spine's copy (written when the run's
 *  first case landed), a copy already fetched from the streaming table, and
 *  what the calculation says it has published now. The tables only ever append,
 *  so indices never move and the longest table is always a superset — the only
 *  real question is whether a fetch is worth doing.
 *
 *  Getting this wrong is invisible rather than loud: a shard row indexing past
 *  the table decodes to null, so a governing-check cell just comes up blank. */
export function worstStringTableChoice(
  spineLength: number,
  cachedLength: number | null,
  publishedCount: number,
): "spine" | "cached" | "refetch" {
  if (publishedCount > (cachedLength ?? spineLength)) return "refetch";
  if (cachedLength !== null && cachedLength >= spineLength) return "cached";
  return "spine";
}

/** Case ids the calculation has published for a run, or null when nothing is
 *  streaming (a finished bundle — every case is available). */
export function readyCaseIds(
  progress: CapacityCalcProgress | null,
  runId: string | null,
): Set<string> | null {
  if (!progress || progress.complete) return null;
  const run = progress.runs.find((r) => r.id === runId) ?? progress.runs[0];
  return new Set(run?.cases_ready ?? []);
}

/** UI selection stashed per run so switching Run (stiffened panel <-> girder)
 *  and back restores what was open and selected. */
interface RunUiMemory {
  activeCaseId: string | null;
  activeMetricId: string;
  selectedModelId: string | null;
  selectedResultId: string | null;
  worstCaseIds: string[];
  worstSummary: CapacityWorstSummary | null;
}

/** One compact row in the worst-over-cases summary (no heavy check detail). */
export interface CapacityWorstRow {
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
  /** Exception text; when present this row outranks every numeric UF. */
  e?: string | null;
}

/** One per-case worst-summary shard, as written to
 *  ``capacity.summary/<case>.json``. v14 splits the former single whole-run
 *  file into these: on large models it grew past the V8 max string length and
 *  ``TextDecoder.decode`` threw, leaving the worst table silently empty. */
export interface CapacityWorstShard {
  format?: string;
  version?: number;
  case_id?: string;
  label?: string;
  rows?: CapacityWorstRowEncoded[];
}

/** A shard row on the wire (v15). The name fields are indices into the run's
 *  shared ``worst_summary.strings`` table — those names are long and recur in
 *  every case's shard, so spelling them out per row dominated the file.
 *  ``u``/``p`` are numbers and stay inline. */
export interface CapacityWorstRowEncoded {
  m: number;
  s?: number | null;
  pg?: number | null;
  u: number | null;
  p: boolean;
  c?: number | null;
  cl?: number | null;
  e?: number | null;
}

/** Shards merged in the store, keyed by case id. Only the cases the user has
 *  ticked for the worst view are ever fetched, so this is usually partial. */
export interface CapacityWorstSummary {
  cases: Record<string, { label?: string; rows: CapacityWorstRow[] }>;
}

/** One check that raised. It is also represented as a failed case-result row;
 *  this compact spine entry drives the banner and red 3D definition edges. */
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
  /** v14: pointer to the per-case worst-over-cases summary shards. Replaces the
   *  v7-v13 ``worst_summary_url`` single-file pointer. */
  worst_summary?: CapacityCaseDetailPointer;
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
  /** Set when one or more shards failed to load, so the worst table can say the
   *  numbers are incomplete instead of quietly showing a partial maximum. */
  worstSummaryError: string | null;
  /** Live calculation progress while a streaming run fills the bundle. */
  calcProgress: CapacityCalcProgress | null;
  /** Selection state remembered per run id (restored on run switch). */
  runUiMemory: Record<string, RunUiMemory>;
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
  /** Merge freshly fetched per-case shards into the accumulated summary. */
  mergeWorstSummaryCases: (
    cases: Record<string, { label?: string; rows: CapacityWorstRow[] }>,
  ) => void;
  setWorstSummaryLoading: (loading: boolean) => void;
  setWorstSummaryError: (error: string | null) => void;
  setCalcProgress: (progress: CapacityCalcProgress | null) => void;
  /** Swap in a newer spine without disturbing the view.
   *
   *  A streaming run rewrites the spine as it goes, so it gets re-read while
   *  the user is already reading results. setCapacityData would reset the
   *  active case, the worst-case subset and the selection every few seconds;
   *  this keeps all of that and only replaces the data. */
  refreshResults: (results: CapacityResults) => void;
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

/** Which Case a run opens on. With more than one load case the useful default
 *  is the worst over all of them — a single case in isolation says nothing
 *  about which one governs — so both run types (stiffened panel and girder)
 *  start there. A single-case run has no worst to take, so it opens on it. */
function defaultCaseId(run: CapacityRun | null | undefined): string | null {
  const cases = run?.result_cases ?? [];
  if (cases.length > 1) return WORST_CASE_ID;
  return cases[0]?.id ?? run?.case_results?.[0]?.case_id ?? null;
}

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
  worstSummaryError: null,
  calcProgress: null,
  runUiMemory: {},
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  setCapacityData: (manifest, source, results) => {
    const run = pickRun(results, manifest.default_run_id);
    const caseId = defaultCaseId(run);
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
      worstSummaryError: null,
      calcProgress: null,
      runUiMemory: {},
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
  mergeWorstSummaryCases: (cases) =>
    set((state) => ({
      worstSummary: { cases: { ...state.worstSummary?.cases, ...cases } },
    })),
  setWorstSummaryLoading: (worstSummaryLoading) => set({ worstSummaryLoading }),
  setWorstSummaryError: (worstSummaryError) => set({ worstSummaryError }),
  setCalcProgress: (calcProgress) => set({ calcProgress }),
  refreshResults: (results) =>
    set((state) => {
      const run =
        results.runs.find((r) => r.id === state.activeRunId) ?? results.runs[0];
      const caseIds = run?.result_cases?.map((c) => c.id) ?? [];
      // Cases the run has grown since the last spine: adopt them into the
      // worst subset, since the user's intent was "all of them".
      const known = new Set(state.worstCaseIds);
      const grew = caseIds.filter((id) => !known.has(id));
      return {
        results,
        activeRunId: state.activeRunId ?? run?.id ?? null,
        activeCaseId: state.activeCaseId ?? defaultCaseId(run),
        worstCaseIds:
          state.worstCaseIds.length === 0
            ? caseIds
            : [...state.worstCaseIds, ...grew],
        loading: false,
        error: null,
      };
    }),
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
      worstSummaryError: null,
      calcProgress: null,
      runUiMemory: {},
      loading: false,
      error: null,
    }),
  // Runs carry disjoint case ids (the girder run g-prefixes them) and their own
  // worst summary, so switching runs swaps the active case, selection, metric
  // and worst-case subset. The outgoing run's state is stashed in runUiMemory
  // and restored when the user switches back (panel <-> girder round trips keep
  // what was open and selected); a first visit starts from the run's defaults.
  setActiveRunId: (activeRunId) =>
    set((state) => {
      const run =
        state.results?.runs.find((r) => r.id === activeRunId) ??
        state.results?.runs[0] ??
        null;
      const runUiMemory = { ...state.runUiMemory };
      if (state.activeRunId) {
        runUiMemory[state.activeRunId] = {
          activeCaseId: state.activeCaseId,
          activeMetricId: state.activeMetricId,
          selectedModelId: state.selectedModelId,
          selectedResultId: state.selectedResultId,
          worstCaseIds: state.worstCaseIds,
          worstSummary: state.worstSummary,
        };
      }
      const saved = activeRunId ? runUiMemory[activeRunId] : undefined;
      return {
        activeRunId,
        runUiMemory,
        selectedModelId: saved?.selectedModelId ?? null,
        selectedResultId: saved?.selectedResultId ?? null,
        activeCaseId: saved?.activeCaseId ?? defaultCaseId(run),
        // Metric ids are run-specific (panel vs girder checks); fall back to
        // the governing UF, which every run carries.
        activeMetricId: saved?.activeMetricId ?? DEFAULT_METRIC,
        worstCaseIds:
          saved?.worstCaseIds ?? run?.result_cases?.map((c) => c.id) ?? [],
        worstSummary: saved?.worstSummary ?? null,
        worstSummaryLoading: false,
        worstSummaryError: null,
        // calcProgress describes the whole calculation, not one run — switching
        // between the panel and girder checks must not blank the run status.
      };
    }),
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
