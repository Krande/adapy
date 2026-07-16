export interface CodecheckCaseInput {
  name: string;
  check_id: "fe_stiffened" | "fe_girder";
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
