import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  groupCapacityErrors,
  summariseCases,
} from "../../components/capacity/capacityFormat";

/** The DBSW shape: the same failure on two stiffeners, across many cases,
 *  differing only in the numeric value. Listed raw that was 109 lines. */
const RADICAND = (value: string) =>
  `FormulaDomainError: [6.21] Square root of negative value while evaluating ` +
  `C_ys radicand (transverse compression near capacity): ${value}.`;

function err(stiffener: string, caseId: string, value: string) {
  return {
    capacity_model_id: `panelGroup(${stiffener})`,
    panel_group: `panelGroup(${stiffener})`,
    stiffener,
    case_id: caseId,
    message: RADICAND(value),
  };
}

describe("groupCapacityErrors", () => {
  it("collapses the same failure across cases into one kind and one row per model", () => {
    const errors = [
      err("Stiff_1", "14", "-0.7856069318475201"),
      err("Stiff_2", "14", "-0.22422933679644266"),
      err("Stiff_1", "15", "-0.7130820338001902"),
      err("Stiff_2", "15", "-0.278457386376127"),
      err("Stiff_1", "16", "-0.7058719734649502"),
    ];

    const groups = groupCapacityErrors(errors);

    // One kind: the numbers are what differ, and they are stripped.
    assert.equal(groups.length, 1);
    assert.equal(groups[0].count, 5);
    assert.ok(groups[0].kind.includes("C_ys radicand"));
    assert.ok(!groups[0].kind.includes("0.7856"));

    // Two models, worst-affected first, each listing its cases once.
    assert.equal(groups[0].models.length, 2);
    assert.equal(groups[0].models[0].label, "panelGroup(Stiff_1) / Stiff_1");
    assert.deepEqual(groups[0].models[0].cases, ["14", "15", "16"]);
    assert.deepEqual(groups[0].models[1].cases, ["14", "15"]);
    // A full message survives for the tooltip.
    assert.ok(groups[0].models[0].sample.includes("-0.78"));
  });

  it("keeps genuinely different failures apart, most frequent first", () => {
    const groups = groupCapacityErrors([
      err("Stiff_1", "14", "-0.1"),
      err("Stiff_1", "15", "-0.2"),
      {
        capacity_model_id: "m2",
        panel_group: "m2",
        stiffener: "Stiff_9",
        case_id: "14",
        message: "InputValidationError: noisy FE geometry",
      },
    ]);

    assert.equal(groups.length, 2);
    assert.equal(groups[0].count, 2);
    assert.ok(groups[1].kind.includes("InputValidationError"));
  });

  it("returns nothing for a clean run", () => {
    assert.deepEqual(groupCapacityErrors([]), []);
  });
});

describe("summariseCases", () => {
  it("collapses contiguous cases into ranges", () => {
    assert.equal(summariseCases(["14", "15", "16", "17", "18"]), "14-18");
  });

  it("keeps gaps visible", () => {
    assert.equal(summariseCases(["14", "15", "20", "31", "32"]), "14-15, 20, 31-32");
  });

  it("handles a single case", () => {
    assert.equal(summariseCases(["14"]), "14");
  });

  it("truncates a long list of disjoint cases", () => {
    const out = summariseCases(["1", "3", "5", "7", "9", "11", "13", "15"]);
    assert.ok(out.endsWith("more"), out);
  });

  it("handles the girder run's prefixed ids", () => {
    assert.equal(summariseCases(["g9", "g10", "g11"]), "g9-g11");
  });
});
