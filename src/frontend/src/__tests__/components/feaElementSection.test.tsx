// A picked FEA element in the flat Properties layout (Fem > Model): an ELEMENT badge and its number,
// then its type and nodes, its section with its dimensions, its plate thickness and its material --
// one group each, no folded "Properties" heading. Drawn only while the panel is flat.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { PropertyItemLayout } = await import("@/components/info_box_selected_object/cellBuilderSelection/PropertyItemLayout");
const { feaElementGroups } = await import("@/components/info_box_selected_object/FeaElementSection");
const { materialFields, sectionFields } = await import("@/components/info_box_selected_object/ObjectMetadataPanel");
const { CORE_PROPERTY_PROVIDERS } = await import("@/components/info_box_selected_object/ObjectInfoBoxComponent");

const data = {
  label: 12,
  elemType: "quad",
  typeName: "FQUS",
  instance: null,
  nodes: [1, 2, 5, 4],
  thickness: 0.012,
  section: { name: "HP200x10", type: "HP", h: 0.2, t_w: 0.01 },
  material: { name: "S355", E: 2.1e11, v: 0.3, rho: 7850, sig_y: 3.55e8 },
  hasProps: true,
};

test("an element's groups: element, section, plate, material, with units", () => {
  const html = renderToStaticMarkup(React.createElement(PropertyItemLayout, { badge: "Element", name: "12", groups: feaElementGroups(data) }));
  const order = ["Element", "Section", "Plate", "Material"].map((g) => html.indexOf(`data-property-group="${g}"`));
  assert.ok(order.every((w, i) => w >= 0 && (i === 0 || w > order[i - 1])), `${order}`);
  assert.match(html, /quad \(FQUS\)/);
  assert.match(html, /1, 2, 5, 4/);
  assert.match(html, /HP200x10/);
  assert.match(html, /12\.0 mm/);
  assert.match(html, /S355/);
  assert.match(html, /210 GPa/);
  assert.match(html, /355 MPa/);
  assert.doesNotMatch(html, /aria-expanded/, "nothing folded");
  assert.deepEqual(sectionFields(data.section), [["Profile", "HP"], ["h", "200.0 mm"], ["t_w", "10.0 mm"]]);
  assert.deepEqual(materialFields(data.material).map(([l]) => l), ["E", "ν", "ρ", "σ_y"]);
  // A beam with no thickness and no section record: only what it has.
  const beam = feaElementGroups({ ...data, thickness: null, section: null });
  assert.deepEqual(beam.map((g) => g.title), ["Element", "Material"]);
});

test("the fea-element section is a provider for an FEA element pick, drawn in the flat panel only", () => {
  const p = CORE_PROPERTY_PROVIDERS.find((x) => x.id === "fea-element")!;
  assert.ok(p, "registered");
  const base = { kind: "mesh" as const, name: "E12", count: 1, hasEntities: true, cellBuilderActive: false };
  assert.equal(p.match({ ...base, meshName: "E12" }), true);
  assert.equal(p.match({ ...base, meshName: "beam_1" }), false, "not a CAD pick");
  const order = (id: string) => CORE_PROPERTY_PROVIDERS.find((x) => x.id === id)!.order!;
  assert.ok(order("object-metadata") < order("fea-element") && order("fea-element") < order("fea-sets"));
});
