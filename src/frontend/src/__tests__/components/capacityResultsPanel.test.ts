import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import CapacityResultsPanel from "../../components/capacity/CapacityResultsPanel";
import type {
  CapacityCaseResult,
  CapacityRun,
} from "../../state/capacityResultsStore";

const ROW: CapacityCaseResult = {
  id: "row-1",
  case_id: "case-1",
  case_label: "Case 1",
  capacity_model_id: "model-1",
  panel_group: "panelGroup(A)",
  stiffener: "Stiffener_A",
  governing_usage: 0.92,
  governing_check: "Plate buckling",
  governing_clause: "6.4",
  passed: true,
  checks: [
    {
      id: "plate",
      label: "Plate buckling",
      clause: "6.4",
      equations: ["(6.10)"],
      usage: 0.92,
      passed: true,
      demand: 10,
      resistance: 20,
      unit: "kN",
      intermediates: {
        lambda_p: 0.456,
        method: "SCM2",
      },
      assumptions: ["Continuous stiffener"],
    },
    {
      id: "advisory",
      label: "Advisory review",
      clause: "6.4.3",
      equations: ["(6.17)"],
      usage: 0.2,
      passed: true,
      advisory: true,
      warnings: ["Mesh density should be reviewed."],
    },
    {
      id: "failed-advisory",
      label: "Failed advisory review",
      clause: "8.1",
      equations: ["(8.2)"],
      usage: 1.2,
      passed: false,
      advisory: true,
    },
  ],
};

const RUN: CapacityRun = {
  id: "run-1",
  result_cases: [{ id: "case-1" }],
  capacity_models: [],
  check_catalog: [],
  case_results: [ROW],
  visual_fields: [],
};

describe("CapacityResultsPanel", () => {
  it("renders a single collapsible checks section with formula references and details", () => {
    const html = renderToStaticMarkup(
      React.createElement(CapacityResultsPanel, {
        run: RUN,
        row: ROW,
        onClose: () => undefined,
      }),
    );

    assert.match(html, /Governing UF/);
    assert.match(html, /0\.920/);
    assert.match(html, /OK/);
    assert.equal(html.match(/>Checks</g)?.length, 1);
    assert.doesNotMatch(html, /Detailed calculation trace/);
    assert.equal(html.match(/<details/g)?.length, 3);
    assert.match(html, /<details open=""/);
    assert.match(html, /DNV-RP-C201 6\.4 \(6\.10\)/);
    assert.match(html, /DNV-RP-C201 6\.4\.3 \(6\.17\)/);
    assert.match(html, /Intermediate values/);
    assert.match(html, /Lambda P/);
    assert.match(html, /0\.456/);
    assert.match(html, /Demand/);
    assert.match(html, /10\.000 kN/);
    assert.match(html, /Resistance/);
    assert.match(html, /20\.000 kN/);
    assert.match(html, /ADVISORY/);
    assert.match(html, /Failed advisory review/);
    assert.match(html, /border-red-500\/50 bg-red-900\/50 text-red-200/);
  });
});
