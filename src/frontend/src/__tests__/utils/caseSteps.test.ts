import assert from "node:assert/strict";
import { test } from "node:test";

import type { FeaCombinationStep, FeaManifest, FeaManifestField } from "../../services/viewerApi";
import {
  caseStatusText,
  clampSlot,
  hasLazyCases,
  legendScopeFor,
  mergeCaseSteps,
  nextCaseToPrefetch,
  slotCount,
  slotRef,
} from "../../utils/scene/fea/caseSteps";

function field(category: FeaManifestField["category"] = "stress", n = 2): FeaManifestField {
  return {
    name_canonical: category === "property" ? "props.thickness" : "sesam.nodes.g_stress",
    name_native: "x",
    kind: "scalar",
    category,
    support: "nodal",
    analysis_kind: "static",
    components: ["SIGXX"],
    n_steps: n,
    steps: Array.from({ length: n }, (_, i) => ({ i, value: i + 1, label: String(i + 1), name: `lc${i + 1}` })),
    scalar_range: { SIGXX: [0, 1] },
    default_view: { reduction: "SIGXX", colormap: "viridis" },
  } as FeaManifestField;
}

function combo(n: number, extra: Partial<FeaCombinationStep> = {}): FeaCombinationStep {
  return {
    n,
    complex: false,
    terms: [[1, 1.2, 0]],
    coefficients: [[1.2, 0]],
    needs_raw: false,
    recipe_hash: `${n}`.padStart(64, "a"),
    ...extra,
  };
}

const V3: Pick<FeaManifest, "result_cases" | "baked_steps" | "combination_steps"> = {
  baked_steps: [1, 2],
  result_cases: [{ n: 1 }, { n: 2 }, { n: 101, combination: true, makeup: "1.2·lc1" }],
};

const V4: Pick<FeaManifest, "result_cases" | "baked_steps" | "combination_steps"> = {
  ...V3,
  result_cases: [...V3.result_cases!, { n: 102, combination: true, makeup: "0.9·lc2" }],
  // Out of order on purpose: the picker lists them by case number.
  combination_steps: [combo(102, { name: "comb102", needs_raw: true }), combo(101, { name: "comb101", makeup: "1.2·lc1" })],
};

test("a bake_version 3 manifest gives exactly the field's steps", () => {
  const f = field();
  const slots = mergeCaseSteps(V3, f);
  assert.deepEqual(slots.map((s) => s.ref), [{ stored: 0 }, { stored: 1 }]);
  assert.deepEqual(slots.map((s) => s.name), ["lc1", "lc2"]);
  assert.equal(slotCount(V3, f), 2);
  assert.equal(hasLazyCases(V3), false);
  assert.deepEqual(slotRef(V3, f, 1), { stored: 1 });
});

test("bake_version 4: stored steps keep their index, combinations follow by case number", () => {
  const f = field();
  const slots = mergeCaseSteps(V4, f);
  assert.deepEqual(
    slots.map((s) => [s.slot, s.kind, s.value]),
    [
      [0, "stored", 1],
      [1, "stored", 2],
      [2, "combination", 101],
      [3, "combination", 102],
    ],
  );
  // makeup from the combination itself, else from result_cases.
  assert.equal(slots[2].makeup, "1.2·lc1");
  assert.equal(slots[3].makeup, "0.9·lc2");
  assert.equal(slots[3].needsRaw, true);
  assert.equal(slots[2].name, "comb101");
  assert.equal(slotCount(V4, f), 4);
  assert.deepEqual(slotRef(V4, f, 0), { stored: 0 });
  assert.deepEqual(slotRef(V4, f, 2), { case: 101 });
  assert.deepEqual(slotRef(V4, f, 3), { case: 102 });
});

test("a model property takes no combination slots", () => {
  const p = field("property", 1);
  assert.equal(mergeCaseSteps(V4, p).length, 1);
  assert.equal(slotCount(V4, p), 1);
  assert.deepEqual(slotRef(V4, p, 3), { stored: 0 });
  assert.equal(clampSlot(V4, p, 3), 0);
  assert.equal(clampSlot(V4, field(), 3), 3);
});

test("without a field the stored steps come from baked_steps, named from result_cases", () => {
  const slots = mergeCaseSteps({ ...V4, result_cases: [{ n: 1, name: "dead" }] }, null);
  assert.deepEqual(slots.map((s) => [s.kind, s.value]), [
    ["stored", 1],
    ["stored", 2],
    ["combination", 101],
    ["combination", 102],
  ]);
  assert.equal(slots[0].name, "dead");
});

test("status text per case", () => {
  assert.equal(caseStatusText(undefined), "computed on request");
  assert.equal(caseStatusText({ status: "on-request" }), "computed on request");
  assert.equal(caseStatusText({ status: "computing", progress: 0.42 }), "computing… 42%");
  assert.equal(caseStatusText({ status: "computing" }), "computing…");
  assert.equal(caseStatusText({ status: "ready", computed: false }), "cached");
  assert.equal(caseStatusText({ status: "ready", computed: true }), "computed");
  assert.equal(caseStatusText({ status: "error", error: "x" }), "failed");
});

test("the next combination is prefetched while walking the list", () => {
  const slots = mergeCaseSteps(V4, field());
  assert.equal(nextCaseToPrefetch(slots, 0), null); // next is a stored step
  assert.equal(nextCaseToPrefetch(slots, 1), 101);
  assert.equal(nextCaseToPrefetch(slots, 2), 102);
  assert.equal(nextCaseToPrefetch(slots, 3), null); // wraps to a stored step
});

test("legend scope: case, stored, envelope, or nothing to say", () => {
  const none = legendScopeFor({ hasCombinations: false, isCase: false, envelopeMode: true, envelopeAvailable: true });
  assert.deepEqual(none, { scope: "all", label: null });
  assert.deepEqual(
    legendScopeFor({ hasCombinations: true, isCase: false, envelopeMode: false, envelopeAvailable: false }),
    { scope: "stored", label: "over stored cases" },
  );
  assert.deepEqual(
    legendScopeFor({ hasCombinations: true, isCase: true, envelopeMode: false, envelopeAvailable: true }),
    { scope: "case", label: "this case" },
  );
  // Asked for the envelope but the server has none: the case's own range, said so.
  assert.deepEqual(
    legendScopeFor({ hasCombinations: true, isCase: true, envelopeMode: true, envelopeAvailable: false }),
    { scope: "case", label: "this case" },
  );
  assert.deepEqual(
    legendScopeFor({ hasCombinations: true, isCase: false, envelopeMode: true, envelopeAvailable: true }),
    { scope: "envelope", label: "over all combinations" },
  );
});
