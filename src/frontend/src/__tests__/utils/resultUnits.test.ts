import assert from "node:assert/strict";
import { test } from "node:test";

import type { FeaManifestField } from "../../services/viewerApi";
import { selectedResultUnit } from "../../utils/scene/fea/resultUnits";

function field(): FeaManifestField {
  return {
    name_canonical: "sesam.elements.g_force",
    name_native: "sesam.elements.g_force",
    kind: "vector6",
    category: "other",
    support: "element_nodal",
    analysis_kind: "static",
    components: ["NXX", "MXX"],
    component_units: ["N", "N·m"],
    n_steps: 1,
    steps: [{ i: 0, value: 1, label: "1" }],
    scalar_range: { NXX: [0, 1], MXX: [0, 1] },
    default_view: { reduction: "NXX", colormap: "viridis" },
  };
}

test("selectedResultUnit follows the selected mixed-dimension component", () => {
  const value = field();

  assert.equal(selectedResultUnit(value, "NXX"), "N");
  assert.equal(selectedResultUnit(value, "MXX"), "N·m");
});

test("selectedResultUnit falls back to a legacy field-level unit", () => {
  const value = field();
  value.component_units = undefined;
  value.unit = "Pa";

  assert.equal(selectedResultUnit(value, "SIGXX"), "Pa");
});
