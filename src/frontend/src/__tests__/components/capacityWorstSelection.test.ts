import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  isSameResultRow,
  resolveWorstSelection,
  type CapacityCaseResultLike,
} from "../../components/capacity/capacityFormat";

/** A compact worst-view row: keyed (model, stiffener), carrying the case its
 *  maximum came from and no per-check detail. */
function worstRow(
  model: string,
  stiffener: string,
  caseId: string,
  uf: number,
  extra: Partial<CapacityCaseResultLike> = {},
): CapacityCaseResultLike {
  return {
    id: `${model}::${stiffener}`,
    case_id: caseId,
    capacity_model_id: model,
    panel_group: `panelGroup(${stiffener})`,
    stiffener,
    governing_usage: uf,
    passed: uf < 1,
    checks: [],
    ...extra,
  } as CapacityCaseResultLike;
}

/** The full row for the same item inside one case's detail file: its own
 *  case-qualified id, and real checks. */
function detailRow(
  model: string,
  stiffener: string,
  caseId: string,
  uf: number,
): CapacityCaseResultLike {
  return {
    id: `${caseId}::${model}::${stiffener}`,
    case_id: caseId,
    capacity_model_id: model,
    panel_group: `panelGroup(${stiffener})`,
    stiffener,
    governing_usage: uf,
    passed: uf < 1,
    checks: [
      {
        id: "plate",
        label: "Plate buckling",
        usage: uf,
        passed: uf < 1,
      },
    ],
  } as CapacityCaseResultLike;
}

const WORST_ROWS = [
  worstRow("model-a", "Stiff_1", "16", 2.91),
  worstRow("model-a", "Stiff_2", "20", 1.4),
  worstRow("model-b", "Stiff_3", "16", 0.6),
];

describe("resolveWorstSelection", () => {
  it("resolves the clicked row and reports the case its maximum came from", () => {
    const { worstRow: hit, row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {},
      selectedResultId: "model-a::Stiff_2",
      selectedModelId: "model-a",
    });

    // The case is reported so the caller can fetch that detail file -- but
    // resolving must never imply switching the active case (issue #35).
    assert.equal(hit?.case_id, "20");
    assert.equal(hit?.stiffener, "Stiff_2");
    // Detail not loaded yet: fall back to the compact row so the panel is
    // never blank.
    assert.equal(row, hit);
  });

  it("prefers the governing case's detailed row once it has loaded", () => {
    const { worstRow: hit, row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {
        "20": [
          detailRow("model-b", "Stiff_3", "20", 0.1),
          detailRow("model-a", "Stiff_2", "20", 1.4),
        ],
      },
      selectedResultId: "model-a::Stiff_2",
      selectedModelId: "model-a",
    });

    assert.equal(hit?.case_id, "20");
    // Matched across differing id schemes: worst rows are keyed
    // (model, stiffener) while detail rows carry a case-qualified id.
    assert.notEqual(row, hit);
    assert.equal(row?.id, "20::model-a::Stiff_2");
    assert.equal(row?.checks.length, 1);
  });

  it("does not borrow a detailed row from a different case", () => {
    const { row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: { "16": [detailRow("model-a", "Stiff_2", "16", 0.3)] },
      selectedResultId: "model-a::Stiff_2",
      selectedModelId: "model-a",
    });

    // Stiff_2's worst came from case 20, so case 16's row must not be used.
    assert.equal(row?.governing_usage, 1.4);
    assert.equal(row?.checks.length, 0);
  });

  it("falls back to the model's worst row when only a model is selected", () => {
    // Picking in the 3D view selects a model, not a specific row.
    const { worstRow: hit } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {},
      selectedResultId: null,
      selectedModelId: "model-a",
    });

    assert.equal(hit?.stiffener, "Stiff_1");
    assert.equal(hit?.governing_usage, 2.91);
  });

  it("ranks an errored row above every finite utilization", () => {
    const errored = worstRow("model-a", "Stiff_9", "31", 0, {
      error: "FormulaDomainError: [6.21] negative radicand",
      passed: false,
    });
    const { worstRow: hit } = resolveWorstSelection({
      worstRows: [...WORST_ROWS, errored],
      caseDetail: {},
      selectedResultId: null,
      selectedModelId: "model-a",
    });

    assert.equal(hit?.stiffener, "Stiff_9");
  });

  it("returns nothing when there is no selection", () => {
    const { worstRow: hit, row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {},
      selectedResultId: null,
      selectedModelId: null,
    });

    assert.equal(hit, null);
    assert.equal(row, null);
  });
});

describe("isSameResultRow", () => {
  it("matches a worst row against the same item's detail row", () => {
    // The table lists worst rows keyed (model, stiffener) while the selection
    // resolves to a case-qualified detail row. Comparing keys leaves the
    // selected row unhighlighted (#46); identity is what actually matches.
    const worst = worstRow("model-a", "Stiff_2", "20", 1.4);
    const detail = detailRow("model-a", "Stiff_2", "20", 1.4);

    assert.notEqual(worst.id, detail.id);
    assert.ok(isSameResultRow(worst, detail));
  });

  it("matches the same item across different cases", () => {
    assert.ok(
      isSameResultRow(
        detailRow("model-a", "Stiff_2", "16", 0.3),
        detailRow("model-a", "Stiff_2", "20", 1.4),
      ),
    );
  });

  it("separates different stiffeners in the same capacity model", () => {
    assert.equal(
      isSameResultRow(
        worstRow("model-a", "Stiff_1", "16", 2.91),
        worstRow("model-a", "Stiff_2", "20", 1.4),
      ),
      false,
    );
  });

  it("is false when either row is missing", () => {
    const row = worstRow("model-a", "Stiff_1", "16", 2.91);
    assert.equal(isSameResultRow(null, row), false);
    assert.equal(isSameResultRow(row, undefined), false);
    assert.equal(isSameResultRow(null, null), false);
  });
});
