import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildCodecheckCasePayload } from "../../services/codecheckCase";

describe("codecheck case export", () => {
  it("emits the standard-aware case envelope without changing values", () => {
    const values = { fy: 355, sigma_x: 120 };
    const payload = buildCodecheckCasePayload({
      name: "panel case",
      check_id: "fe_stiffened",
      capacity_model_id: "panel-1",
      case_id: "case-10",
      values,
    });

    assert.equal(payload.schema, "codecheck/case@1");
    assert.equal(payload.standard_id, "dnv-rp-c201");
    assert.equal(payload.check_id, "fe_stiffened");
    assert.equal(payload.source, "adapy-capacity-viewer");
    assert.deepEqual(payload.values, values);
  });
});
