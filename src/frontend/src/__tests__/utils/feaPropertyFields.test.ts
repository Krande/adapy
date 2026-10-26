import assert from "node:assert/strict";
import { test } from "node:test";

// The Field picker's "Model" group: colour by what the elements are (superelement,
// material, section, thickness), apart from the results tree, which never lists them.

import { buildFeaPropertyFields, buildFeaResultHierarchy } from "@/utils/scene/fea/resultHierarchy";
import type { FeaManifestField } from "@/services/viewerApi";

function field(name: string, category: string, group_path?: string[]): FeaManifestField {
  return { name_canonical: name, category, group_path } as unknown as FeaManifestField;
}

test("property fields are listed with readable labels, results are not", () => {
  const fields = [
    field("sesam.nodes.displacement", "displacement", ["Nodes", "Displacement"]),
    field("props.super_element", "property", ["Properties", "SUPERELEMENT"]),
    field("props.material", "property", ["Properties", "MATERIAL"]),
    field("props.thickness", "property"),
  ];

  const props = buildFeaPropertyFields(fields);

  assert.deepEqual(
    props.map((p) => [p.label, p.field.name_canonical]),
    [
      ["Superelement", "props.super_element"],
      ["Material", "props.material"],
      ["Thickness", "props.thickness"],
    ],
  );
  // ...and the results hierarchy still keeps them out.
  const { positions, ungrouped } = buildFeaResultHierarchy(fields);
  const listed = [...positions.flatMap((p) => p.attributes.map((a) => a.field)), ...ungrouped];
  assert.ok(listed.every((f) => f.category !== "property"));
});
