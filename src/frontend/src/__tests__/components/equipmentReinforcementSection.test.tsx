// An equipment's REINFORCEMENT_SECTION (the section that reinforces the structure it is
// seated in) had no field anywhere in Properties -- SelectionSection.tsx's useEquipmentGroups only
// covered Placement (SPACE_LOC), Mass and Systems. Added as a plain text field (fields.tsx's
// ParamText), round-tripping through the cell's params like any other engine column.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { ObjectInfoBody } = await import("@/components/info_box_selected_object/ObjectInfoBoxComponent");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

const st = () => useCellBuilderStore.getState();
const pristine = { ...useCellBuilderStore.getInitialState() };
/** A server render reads getInitialState(); sync the live state in, render, restore. */
function inViewer(el: React.ReactElement): string {
  Object.assign(useCellBuilderStore.getInitialState(), st());
  try {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));
  } finally {
    Object.assign(useCellBuilderStore.getInitialState(), pristine);
  }
}

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "deckA", X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 3 }],
  equipments: [{ NAME: "eq_1", SPACE_NAME: "deckA", SPACE_LOC: "FLOOR", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 2, REINFORCEMENT_SECTION: "TG400x200x20x30" }],
} as unknown as ProceduralDoc;

test("the equipment's REINFORCEMENT_SECTION shows in Properties, pre-filled from the doc", () => {
  st().open("m", "Model", 1, doc);
  try {
    const eq = Object.values(st().cells).find((c) => c.kind === "equipment")!;
    st().setSelection({ kind: "cell", cellId: eq.id });
    const html = inViewer(React.createElement(ObjectInfoBody));
    assert.match(html, /data-field="Reinforcement section">/, "the field is offered");
    const m = /aria-label="Reinforcement section \(REINFORCEMENT_SECTION\)"[^>]*value="([^"]*)"/.exec(html);
    assert.ok(m, "a text input named for the column");
    assert.equal(m![1], "TG400x200x20x30", "pre-filled from the doc");
  } finally {
    st().close();
  }
});

test("REINFORCEMENT_SECTION round-trips through params and the written doc, like any other engine column", () => {
  st().open("m", "Model", 1, doc);
  try {
    const eq = Object.values(st().cells).find((c) => c.kind === "equipment")!;
    st().setCellParam(eq.id, "REINFORCEMENT_SECTION", "SHS200x10");
    const after = Object.values(st().cells).find((c) => c.id === eq.id)!;
    assert.equal(after.params.REINFORCEMENT_SECTION, "SHS200x10");
    const written = (st().toDoc().equipments ?? []).find((e) => e.NAME === after.name)!;
    assert.equal(written.REINFORCEMENT_SECTION, "SHS200x10");
    const html = inViewer((() => {
      st().setSelection({ kind: "cell", cellId: eq.id });
      return React.createElement(ObjectInfoBody);
    })());
    assert.match(html, /aria-label="Reinforcement section \(REINFORCEMENT_SECTION\)"[^>]*value="SHS200x10"/);
  } finally {
    st().close();
  }
});

test("an equipment with no REINFORCEMENT_SECTION of its own shows the field empty, not absent", () => {
  st().open("m", "Model", 1, { ...doc, equipments: [{ NAME: "pump", SPACE_NAME: "deckA", SPACE_LOC: "FLOOR", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1 }] } as unknown as ProceduralDoc);
  try {
    const eq = Object.values(st().cells).find((c) => c.kind === "equipment")!;
    st().setSelection({ kind: "cell", cellId: eq.id });
    const html = inViewer(React.createElement(ObjectInfoBody));
    assert.match(html, /aria-label="Reinforcement section \(REINFORCEMENT_SECTION\)"[^>]*value=""/);
  } finally {
    st().close();
  }
});
