// PropertyItemLayout, the one layout of a topology item in the Properties panel, and its Field
// row: the parents line first, then the header, the geometry grid and the groups; a field's help
// on its label; inline fields share a row; geometry is read-only without onChange.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { Field, PropertyItemLayout } = await import("@/components/info_box_selected_object/cellBuilderSelection/PropertyItemLayout");
const { readFileSync } = await import("node:fs");

const h = React.createElement;

test("parents, header, geometry, then the groups, in that order", () => {
  const html = renderToStaticMarkup(
    h(PropertyItemLayout, {
      badge: "Pump",
      name: "P-1",
      onRename: () => {},
      parents: { space: { name: "S1", onSelect: () => {} }, structure: { name: "Upper", blueprint: "plate" } },
      geometry: { origin: [1, 2, 3], size: [1, 1, 1], onChange: () => {}, editable: { size: false } },
      groups: [
        { title: "Placement", fields: h(Field, { label: "Location", help: "Floor or roof" }, "FLOOR") },
        { title: "CAD", fields: h(Field, { label: "Grid", inline: true }, h("i", null, "X"), h("i", null, "Y")) },
      ],
    }),
  );
  const where = ["data-property-parents", "data-property-header", 'data-property-group="Geometry"', 'data-property-group="Placement"', 'data-property-group="CAD"'].map(
    (m) => html.indexOf(m),
  );
  assert.ok(where.every((w, i) => w >= 0 && (i === 0 || w > where[i - 1])), `in order: ${where}`);
  assert.match(html, />in<\/span><button[^>]*title="Select space S1">S1<\/button><span aria-hidden="true">·<\/span><button[^>]*disabled=""[^>]*>Upper<\/button><span[^>]*>\[plate\]<\/span>/);
  assert.match(html, /<input type="text" aria-label="Name of pump P-1"[^>]*value="P-1"/, "renamed in place");
  assert.match(html, /<input[^>]*aria-label="Position X"/);
  assert.doesNotMatch(html, /<input[^>]*aria-label="Size X"/, "size not editable here");
  assert.match(html, /title="Floor or roof">Location<\/span>/, "the label carries its help");
  assert.match(html, /data-field="Grid"><span[^>]*>Grid<\/span><div class="flex min-w-0 flex-wrap[^"]*"><i>X<\/i><i>Y<\/i>/, "inline: one row");
});

test("no rename, no onChange: plain name and read-only geometry; no parents, no line", () => {
  const html = renderToStaticMarkup(h(PropertyItemLayout, { badge: "Marker", name: "m", geometry: { origin: [0, 0, 0], size: [1, 1, 1] } }));
  assert.doesNotMatch(html, /data-property-parents/);
  assert.doesNotMatch(html, /<input/);
  assert.match(html, /font-semibold">m<\/span>/);
});

test("exported for plugins on @/viewer-core/layout", () => {
  const src = readFileSync(new URL("../../viewer-core/layout.ts", import.meta.url), "utf8");
  assert.match(src, /export \{ Field, FieldCheck, PropertyItemLayout \} from "@\/components\/info_box_selected_object\/cellBuilderSelection\/PropertyItemLayout"/);
});
