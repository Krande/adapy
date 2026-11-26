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
  assert.match(src, /export \{ Field, FieldCheck, PropertyGroup, PropertyItemLayout \} from "@\/components\/info_box_selected_object\/cellBuilderSelection\/PropertyItemLayout"/);
});

// ── engine UI labels: a {list, column} help can override the shown label, opt-in ──────────────

test("without useEngineLabel, a {list, column} help never replaces the caller's label: a row borrowing one column's help for several inputs would otherwise show the column's full sentence instead of its own short label", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { setServerState } = await import("../fixtures/panelRealm");
  const { FieldCheck } = await import("@/components/info_box_selected_object/cellBuilderSelection/PropertyItemLayout");
  // zustand's useStore reads getInitialState() as the SSR snapshot (renderToStaticMarkup below),
  // not getState() -- setServerState sets both (same gotcha builderSections.test.tsx works around).
  setServerState(useCellBuilderStore, {
    engines: [
      {
        slug: "adapy-default",
        field_help: {
          spaces: { GRID_X_CREATE: "Create the X grid lines." },
          "spaces.labels": { GRID_X_CREATE: "Include in X grid" },
        },
      },
    ],
    selectedEngine: "adapy-default",
  } as never);
  try {
    const html = renderToStaticMarkup(h(Field, { label: "Create", inline: true, help: { list: "spaces", column: "GRID_X_CREATE" } }, "x"));
    // "Create" stays "Create" -- not swapped for the X column's own label.
    assert.match(html, />Create<\/span>/);
    assert.doesNotMatch(html, /Include in X grid/);
    assert.match(html, /data-field="Create"/);

    // FieldCheck follows the same default (ParamCheck's "X" inside that row).
    const checkHtml = renderToStaticMarkup(
      h(FieldCheck, { label: "X", checked: false, onChange: () => {}, help: { list: "spaces", column: "GRID_X_CREATE" } }),
    );
    assert.match(checkHtml, />X<\/label>/);
    assert.doesNotMatch(checkHtml, /Include in X grid/);
  } finally {
    setServerState(useCellBuilderStore, { engines: [], selectedEngine: "adapy-default" } as never);
  }
});

test("useEngineLabel shows the engine's label when it has one, keeping the column name first in the tooltip (standalone single-column editors)", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { setServerState } = await import("../fixtures/panelRealm");
  const { FieldCheck } = await import("@/components/info_box_selected_object/cellBuilderSelection/PropertyItemLayout");
  setServerState(useCellBuilderStore, {
    engines: [
      {
        slug: "adapy-default",
        field_help: {
          spaces: { GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN: "The minimum spacing between primary girders." },
          "spaces.labels": { GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN: "Primary girder spacing, min" },
        },
      },
    ],
    selectedEngine: "adapy-default",
  } as never);
  try {
    const html = renderToStaticMarkup(
      h(Field, { label: "GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN", useEngineLabel: true, help: { list: "spaces", column: "GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN" } }, "x"),
    );
    // The visible text is the engine's label, not the column name...
    assert.match(html, />Primary girder spacing, min<\/span>/);
    // ...but data-field (what a host finds a row by) keeps the caller's own, unaffected.
    assert.match(html, /data-field="GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN"/);
    // ...and the tooltip starts with the column name, so the Excel name stays discoverable.
    assert.match(html, /title="GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN — The minimum spacing between primary girders\."/);

    // FieldCheck follows the same rule when it opts in too.
    const checkHtml = renderToStaticMarkup(
      h(FieldCheck, {
        label: "GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN",
        checked: false,
        onChange: () => {},
        useEngineLabel: true,
        help: { list: "spaces", column: "GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN" },
      }),
    );
    assert.match(checkHtml, />Primary girder spacing, min<\/label>/);
  } finally {
    setServerState(useCellBuilderStore, { engines: [], selectedEngine: "adapy-default" } as never);
  }
});

test("a column without a label keeps showing its column name; the tooltip always names the column first, with or without useEngineLabel", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { setServerState } = await import("../fixtures/panelRealm");
  setServerState(useCellBuilderStore, {
    engines: [{ slug: "adapy-default", field_help: { spaces: { NAME: "Name of space" } } }],
    selectedEngine: "adapy-default",
  } as never);
  try {
    const html = renderToStaticMarkup(h(Field, { label: "NAME", useEngineLabel: true, help: { list: "spaces", column: "NAME" } }, "x"));
    assert.match(html, />NAME<\/span>/);
    assert.match(html, /title="NAME — Name of space"/, "no label active: the tooltip still names the column first");

    // No help text and no label either: the tooltip is the bare column name, not nothing -- a row
    // borrowing another column's help (e.g. "-X"'s SE4) needs it named even with no help text of
    // its own.
    const bare = renderToStaticMarkup(h(Field, { label: "DX", useEngineLabel: true, help: { list: "spaces", column: "DX" } }, "x"));
    assert.match(bare, /title="DX"/);
  } finally {
    setServerState(useCellBuilderStore, { engines: [], selectedEngine: "adapy-default" } as never);
  }
});

// ── one X/Y/Z block: rotation and extraRows share the Geometry grid ────────────────────────

test("geometry.rotation adds a Rotation° row to the SAME grid, writing through the shared onChange", () => {
  const html = renderToStaticMarkup(
    h(PropertyItemLayout, {
      badge: "Pump",
      name: "P-1",
      geometry: { origin: [1, 2, 3], size: [1, 1, 1], rotation: [0, 0, 90], onChange: () => {} },
    }),
  );
  // One grid, one heading row (X Y Z once), Position / Size / Rotation° as its rows.
  assert.equal((html.match(/text-center text-\[10px\] text-gray-500">X</g) ?? []).length, 1, "one X/Y/Z heading, not one per row");
  assert.match(html, /data-property-geometry="">[\s\S]*?Position[\s\S]*?Size[\s\S]*?Rotation°[\s\S]*?aria-label="Rotation° Z"/);
  assert.match(html, /aria-label="Rotation° Z"[^>]*value="90"/);
});

test("extraRows share the grid's X/Y/Z layout and are always editable, regardless of geometry.onChange", () => {
  const html = renderToStaticMarkup(
    h(PropertyItemLayout, {
      badge: "Pump",
      name: "P-1",
      // No geometry.onChange at all: Position/Size are then read-only, but an extra row is
      // unaffected -- its own onChange decides, not geometry.onChange's absence.
      geometry: {
        origin: [1, 2, 3],
        size: [1, 1, 1],
        extraRows: [{ key: "cog", label: "COG", value: [0.1, 0.2, 0.3], onChange: () => {} }],
      },
    }),
  );
  assert.doesNotMatch(html, /<input[^>]*aria-label="Position X"/, "Position is read-only: no geometry.onChange");
  assert.match(html, /<input[^>]*aria-label="COG X"[^>]*value="0.1"/, "COG is editable regardless");
  assert.match(html, /<input[^>]*aria-label="COG Z"[^>]*value="0.3"/);
});
