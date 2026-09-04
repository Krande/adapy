import assert from "node:assert/strict";
import { test } from "node:test";

import type { FeaManifestField } from "../../services/viewerApi";
import { buildFeaResultHierarchy } from "../../utils/scene/fea/resultHierarchy";

function field(name: string, groupPath?: string[]): FeaManifestField {
  return {
    name_canonical: name,
    name_native: name,
    kind: "vectorN",
    category: "stress",
    support: "element_average",
    group_path: groupPath,
    analysis_kind: "static",
    components: ["SIGXX"],
    n_steps: 1,
    steps: [{ i: 0, value: 1, label: "1" }],
    scalar_range: { SIGXX: [0, 1] },
    default_view: { reduction: "SIGXX", colormap: "viridis" },
  };
}

test("buildFeaResultHierarchy preserves manifest position and attribute order", () => {
  const dStress = field("sesam.element_average.d_stress", [
    "Element average",
    "D-STRESS",
  ]);
  const rStress = field("sesam.element_average.r_stress", [
    "Element average",
    "R-STRESS",
  ]);
  const nodal = field("sesam.nodes.g_stress", ["Nodes", "G-STRESS"]);
  const legacy = field("STRESS");

  const hierarchy = buildFeaResultHierarchy([dStress, rStress, nodal, legacy]);

  assert.deepEqual(
    hierarchy.positions.map((position) => position.label),
    ["Element average", "Nodes"],
  );
  assert.deepEqual(
    hierarchy.positions[0].attributes.map((attribute) => attribute.label),
    ["D-STRESS", "R-STRESS"],
  );
  assert.equal(hierarchy.positions[0].attributes[0].field, dStress);
  assert.deepEqual(hierarchy.ungrouped, [legacy]);
});

test("nodal surface variants appear as one semantic tree attribute", () => {
  const upper = field("sesam.nodes.g_stress", ["Nodes", "G-STRESS"]);
  upper.semantic_key = "sesam.nodes.g_stress";
  upper.surface = "upper";
  upper.surface_variants = [
    { surface: "upper", field_name: "sesam.nodes.g_stress" },
    { surface: "lower", field_name: "sesam.nodes.g_stress.lower" },
  ];
  const lower = field("sesam.nodes.g_stress.lower", ["Nodes", "G-STRESS"]);
  lower.semantic_key = upper.semantic_key;
  lower.surface = "lower";
  lower.surface_variants = upper.surface_variants;

  const hierarchy = buildFeaResultHierarchy([upper, lower]);

  assert.equal(hierarchy.positions[0].attributes.length, 1);
  assert.equal(hierarchy.positions[0].attributes[0].field, upper);
});
