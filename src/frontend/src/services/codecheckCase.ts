export interface CodecheckCaseInput {
  name: string;
  // One per capacity-model type: stiffened panels and girders use the web UI's
  // FE checks (Section 5 design stresses -> resultants), while an unstiffened
  // plate field's membrane stresses are the Section-4 inputs directly, so it
  // uses that app's plain "unstiffened_plate" check.
  check_id: "fe_stiffened" | "fe_girder" | "unstiffened_plate";
  capacity_model_id: string;
  case_id: string;
  values: Record<string, unknown>;
}

export function buildCodecheckCasePayload(input: CodecheckCaseInput) {
  return {
    schema: "codecheck/case@1" as const,
    standard_id: "dnv-rp-c201" as const,
    name: input.name,
    check_id: input.check_id,
    source: "adapy-capacity-viewer" as const,
    capacity_model_id: input.capacity_model_id,
    case_id: input.case_id,
    values: input.values,
    saved_at: new Date().toISOString(),
  };
}
