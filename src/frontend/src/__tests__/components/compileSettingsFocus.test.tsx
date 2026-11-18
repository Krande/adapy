// The Compile settings' Blueprint row follows the selection: with a structure (or
// something in it) selected it shows and sets THAT structure's blueprint; with nothing in
// focus, the model-level one. Server-rendered over the panelRealm fixture, like
// componentListTree.test.tsx. Only an engine that supports grouping has structures.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { CompileSettingsBody: CompileSettingsSection } = await import("@/components/viewer/cellbuilder/panel/CompileSettingsSection");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

type Doc = import("@/services/viewerApi").ProceduralDoc;

const doc: Doc = {
  grid: {},
  blueprint: {},
  blueprint_name: "Framework with deck",
  spaces: [
    { NAME: "deckA", X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 4, STRUCTURE_NAME: "Topside" },
    { NAME: "leg", X: 0, Y: 0, Z: -30, DX: 4, DY: 4, DZ: 30, STRUCTURE_NAME: "Jacket" },
  ],
  equipments: [{ NAME: "pump", SPACE_NAME: "deckA", SPACE_LOC: "FLOOR", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1 }],
  openings: [],
  groups: [
    { name: "Topside", blueprint: "Stiffened plate" },
    { name: "Jacket", blueprint: "Framework" },
  ],
};

const engines = [
  { id: "pm", slug: "pm-engine", name: "pm engine", description: null, revision: 1, origin: "local" as const, supports_grouping: true },
];
const blueprints = ["Framework with deck", "Framework", "Stiffened plate"].map((slug) => ({ slug, name: slug, description: "", origin: "code" as const, fields: [] }));

useCellBuilderStore.getState().loadFromDoc(doc);
const idOf = (name: string) => Object.values(useCellBuilderStore.getState().cells).find((c) => c.name === name)!.id;
const sync = () => {
  const s = useCellBuilderStore.getState();
  setServerState(useCellBuilderStore, {
    cells: s.cells,
    groups: s.groups,
    loftMembers: s.loftMembers,
    selection: s.selection,
    selectedCellIds: s.selectedCellIds,
    selectedBlueprint: s.selectedBlueprint,
    selectedEngine: "pm-engine",
    engines,
    blueprints,
    active: { modelId: "m-1", name: "Model A", revision: 2 },
  });
};
const html = () => renderToStaticMarkup(React.createElement(CompileSettingsSection));
const shown = (h: string) => /aria-label="(Blueprint[^"]*)"[^>]*>([\s\S]*?)<\/select>/.exec(h);
const selectedOption = (h: string) => {
  const opt = (shown(h)?.[2] ?? "").match(/<option[^>]* selected=""[^>]*>/)?.[0] ?? "";
  return /value="([^"]*)"/.exec(opt)?.[1];
};

test("nothing selected: the model-level blueprint, and a hint to select a structure", () => {
  useCellBuilderStore.getState().setSelection(null);
  sync();
  const h = html();
  assert.equal(shown(h)?.[1], "Blueprint");
  assert.equal(selectedOption(h), "Framework with deck");
  assert.match(h, /data-blueprint-for=""/);
  assert.match(h, /The model default: select a structure/);
});

test("a space selected: its structure's blueprint, named", () => {
  useCellBuilderStore.getState().setSelection({ kind: "cell", cellId: idOf("leg") });
  sync();
  const h = html();
  assert.equal(shown(h)?.[1], "Blueprint of structure Jacket");
  assert.equal(selectedOption(h), "Framework");
  assert.match(h, /data-blueprint-for="Jacket"/);
  assert.match(h, /Of the structure Jacket \(selected\)/);
});

test("a structure row selected (all its components): that structure; a unit in a space: its space's structure", () => {
  useCellBuilderStore.getState().selectCells([idOf("deckA"), idOf("pump")]);
  sync();
  assert.equal(selectedOption(html()), "Stiffened plate");
  useCellBuilderStore.getState().setSelection({ kind: "cell", cellId: idOf("pump") });
  sync();
  assert.equal(shown(html())?.[1], "Blueprint of structure Topside");
});

test("a selection across structures falls back to the model-level blueprint", () => {
  useCellBuilderStore.getState().selectCells([idOf("deckA"), idOf("leg")]);
  sync();
  const h = html();
  assert.equal(shown(h)?.[1], "Blueprint");
  assert.equal(selectedOption(h), "Framework with deck");
});

test("setting the shown blueprint writes the focused structure's, not the model's", () => {
  const s = useCellBuilderStore.getState();
  s.setSelection({ kind: "cell", cellId: idOf("leg") });
  s.setGroupBlueprint("Jacket", "Framework with deck");
  sync();
  assert.equal(selectedOption(html()), "Framework with deck");
  assert.equal(useCellBuilderStore.getState().selectedBlueprint, "Framework with deck", "unchanged (it happened to be the same)");
  assert.equal(useCellBuilderStore.getState().groups.find((g) => g.name === "Jacket")?.blueprint, "Framework with deck");
  assert.equal(useCellBuilderStore.getState().groups.find((g) => g.name === "Topside")?.blueprint, "Stiffened plate");
});

test("an engine without grouping shows the model-level blueprint whatever is selected", () => {
  useCellBuilderStore.getState().setSelection({ kind: "cell", cellId: idOf("deckA") });
  sync();
  setServerState(useCellBuilderStore, { engines: [{ ...engines[0], supports_grouping: false }] });
  const h = html();
  assert.equal(shown(h)?.[1], "Blueprint");
  assert.doesNotMatch(h, /data-blueprint-scope/);
});
