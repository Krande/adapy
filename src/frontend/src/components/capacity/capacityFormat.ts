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

export function ufTextClass(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "text-gray-400";
  if (value > 1.0) return "text-red-300";
  if (value >= 0.8) return "text-yellow-200";
  return "text-gray-100";
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
