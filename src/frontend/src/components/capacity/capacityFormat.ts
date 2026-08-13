import type {
  CapacityCaseResult,
  CapacityCheckResult,
  CapacityRun,
  CapacityVisualField,
} from "@/state/capacityResultsStore";

export const CAPACITY_FLOATING_PANEL_RIGHT_PX = 16;
export const CAPACITY_FLOATING_PANEL_GAP_PX = 16;
export const CAPACITY_RESULTS_PANEL_WIDTH_PX = 384;
export const CAPACITY_INPUT_RIGHT_WITH_RESULTS_PX =
  CAPACITY_FLOATING_PANEL_RIGHT_PX +
  CAPACITY_FLOATING_PANEL_GAP_PX +
  CAPACITY_RESULTS_PANEL_WIDTH_PX;

export type CapacityRunLike = CapacityRun;
export type CapacityCaseResultLike = CapacityCaseResult;
export type CapacityVisualFieldLike = CapacityVisualField;

export function modeButton(active: boolean): string {
  return (
    "px-2 py-1 rounded-sm border " +
    (active
      ? "bg-blue-600 border-blue-500 text-white"
      : "bg-gray-800 border-gray-600 text-gray-300 hover:bg-gray-700")
  );
}

export function formatUf(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "-";
  return value.toFixed(3);
}

export function ufClass(value: number | null | undefined): string {
  return "px-2 py-1 font-mono " + ufTextClass(value);
}

// Genie "UfTot" discrete bands (thresholds 0.2/0.4/0.6/0.8/1.0), mapped to
// readable text colours that track the genie_uf_color_scheme palette
// (light-blue / cyan / green / yellow / orange / red). No fading between bands.
export function ufTextClass(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "text-gray-400";
  if (value >= 1.0) return "text-red-500";
  if (value >= 0.8) return "text-orange-400";
  if (value >= 0.6) return "text-yellow-300";
  if (value >= 0.4) return "text-green-400";
  if (value >= 0.2) return "text-cyan-300";
  return "text-sky-300";
}

export function shortName(name: string): string {
  return name
    .replace(/^panelGroup\(/, "")
    .replace(/\)$/, "")
    .replace(/^Stiffener_/, "");
}

export function caseResultKey(row: CapacityCaseResultLike): string {
  return (
    row.id ??
    `${row.case_id}::${row.capacity_model_id}::${row.stiffener ?? row.panel_group}`
  );
}

/** Ordering weight for a result row: worst first. */
export function capacityRowScore(row: CapacityCaseResultLike): number {
  // A missing engineering result is more severe than any finite utilization.
  return row.error ? Number.MAX_VALUE : (row.governing_usage ?? -1);
}

/** Identity of a result row across cases: (capacity model, stiffener).
 *  Worst-view rows are keyed this way, while per-case detail rows carry a
 *  case-qualified ``id``, so the two are matched on this rather than on
 *  ``caseResultKey``. */
export function rowIdentity(row: CapacityCaseResultLike): string {
  return `${row.capacity_model_id}::${row.stiffener ?? row.panel_group}`;
}

/** Whether two rows describe the same item, regardless of which case they came
 *  from. Used to mark the selected row in the table: comparing ``caseResultKey``
 *  fails in the worst view, where the table lists (model, stiffener)-keyed rows
 *  while the selection resolves to a case-qualified detail row. Within any one
 *  case an item appears once, so this stays unambiguous in the per-case view. */
export function isSameResultRow(
  a: CapacityCaseResultLike | null | undefined,
  b: CapacityCaseResultLike | null | undefined,
): boolean {
  if (!a || !b) return false;
  return rowIdentity(a) === rowIdentity(b);
}

/** Resolve what the "Worst (over selected cases)" view has selected.
 *
 *  Returns the compact worst row -- which carries the case its maximum came
 *  from, so the caller knows whose detail to fetch -- and the row to display:
 *  the full row from that case once its detail has loaded, otherwise the
 *  compact row so the panel is never blank.
 *
 *  Selecting must not change the active case. The user asked for the worst
 *  over a set of cases, and silently dropping them into one case throws that
 *  away (issue #35). */
export function resolveWorstSelection<T extends CapacityCaseResultLike>(args: {
  worstRows: T[];
  caseDetail: Record<string, CapacityCaseResultLike[]>;
  selectedResultId: string | null;
  selectedModelId: string | null;
}): { worstRow: T | null; row: CapacityCaseResultLike | null } {
  const { worstRows, caseDetail, selectedResultId, selectedModelId } = args;

  let worstRow: T | null = null;
  if (selectedResultId) {
    worstRow =
      worstRows.find((row) => caseResultKey(row) === selectedResultId) ?? null;
  }
  if (!worstRow && selectedModelId) {
    // Picking in the 3D view selects a model, not a specific row.
    worstRow =
      worstRows
        .filter((row) => row.capacity_model_id === selectedModelId)
        .sort((a, b) => capacityRowScore(b) - capacityRowScore(a))[0] ?? null;
  }
  if (!worstRow) return { worstRow: null, row: null };

  const want = rowIdentity(worstRow);
  const detailed =
    (caseDetail[worstRow.case_id] ?? []).find(
      (row) => rowIdentity(row) === want,
    ) ?? null;
  return { worstRow, row: detailed ?? worstRow };
}

/** Human-readable case label for a result row. Worst-view rows carry the case
 *  they came from in ``worstCaseLabel``; otherwise resolve via the run's
 *  ``result_cases`` (falling back to the row's own label / id). */
export function caseLabelForRow(
  run: CapacityRunLike,
  row: CapacityCaseResultLike,
): string {
  const worstLabel = (row as { worstCaseLabel?: string }).worstCaseLabel;
  if (worstLabel) return worstLabel;
  const match = run.result_cases.find((rc) => rc.id === row.case_id);
  return match?.label ?? row.case_label ?? `Case ${row.case_id}`;
}

export function formulaReference(
  check: Pick<CapacityCheckResult, "clause" | "equations">,
): string {
  const clause = check.clause ? `DNV-RP-C201 ${check.clause}` : "DNV-RP-C201";
  const equations = check.equations?.length
    ? ` ${check.equations.join(", ")}`
    : "";
  return `${clause}${equations}`;
}
