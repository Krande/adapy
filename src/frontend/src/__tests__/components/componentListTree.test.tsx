// The Model panel (core's component list) drawn in its three views over a two-structure
// model: the View option, the group rows with their counts and INCLUDE marks, the item
// rows dimmed by their effective include state, and the per-row actions kept.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { ComponentList, ComponentListBody } = await import("@/components/viewer/cellbuilder/panel/ComponentList");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { useModelPanelViewStore } = await import("@/state/modelPanelViewStore");

type Doc = import("@/services/viewerApi").ProceduralDoc;

const doc: Doc = {
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "deckA", X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 4, STRUCTURE_NAME: "Topside" },
    { NAME: "leg", X: 0, Y: 0, Z: -30, DX: 4, DY: 4, DZ: 30, STRUCTURE_NAME: "Jacket" },
  ],
  equipments: [
    { NAME: "pump", SPACE_NAME: "deckA", SPACE_LOC: "ROOF", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1, DESCRIPTION: "pump" },
    { NAME: "tank", INCLUDE: false, SPACE_NAME: "deckA", SPACE_LOC: "FLOOR", X: 5, Y: 5, Z: 0, LX: 2, LY: 2, LZ: 2 },
    { NAME: "buoy", X: -20, Y: -20, Z: 0, LX: 1, LY: 1, LZ: 1, GLOBAL_COORDS: true },
  ],
  openings: [{ NAME: "door", SPACE_NAME: "deckA", SUBTYPE: "door", USE_GLOBAL_COORDS: true, X: 2, Y: 0, Z: 0, DX: 1, DY: 0.2, DZ: 2 }],
  groups: [
    { name: "Topside", blueprint: "steel_stru" },
    { name: "Jacket", blueprint: "jacket", include: false },
  ],
};

// Server rendering reads a store's INITIAL snapshot: load the doc, then mirror what it
// set (and the view option, below) into that snapshot with setServerState.
useCellBuilderStore.getState().loadFromDoc(doc);
{
  const s = useCellBuilderStore.getState();
  setServerState(useCellBuilderStore, { cells: s.cells, groups: s.groups, loftMembers: s.loftMembers, active: { modelId: "m-1", name: "Model A", revision: 2 } });
}
const setView = (view: import("@/utils/cellbuilder/modelTree").ModelView) => {
  useModelPanelViewStore.getState().setView(view);
  setServerState(useModelPanelViewStore, { view });
};
const fold = (key: string, collapsed: boolean) => {
  useModelPanelViewStore.getState().setCollapsed(key, collapsed);
  setServerState(useModelPanelViewStore, { collapsed: useModelPanelViewStore.getState().collapsed });
};

const render = (el: React.ReactElement) => renderToStaticMarkup(el);
const list = () => render(React.createElement(ComponentList));
const rows = (html: string) => [...html.matchAll(/data-component-row="[^"]+"[^>]*>/g)].length;
const groupsOf = (html: string) => [...html.matchAll(/data-component-group="([^"]+)" data-group-kind="([^"]+)" data-include-mark="([^"]+)"/g)].map((m) => `${m[2]}:${m[1].split(":")[1]}=${m[3]}`);
const rowState = (html: string, name: string) => {
  const id = Object.values(useCellBuilderStore.getState().cells).find((c) => c.name === name)?.id;
  return new RegExp(`data-component-row="${id}" data-include-state="([a-z-]+)"`).exec(html)?.[1];
};

test("flat (the default): every component, no group rows, the View option on top", () => {
  setView("flat");
  const html = list();
  assert.match(html, /data-model-view-active="flat"/);
  assert.match(html, /aria-label="Model view"/);
  assert.match(html, /<option[^>]*value="flat"[^>]*>Flat</);
  assert.match(html, /<option[^>]*value="cell"[^>]*>By structure and cell</);
  assert.equal(rows(html), 6);
  assert.deepEqual(groupsOf(html), []);
  // The per-row actions are still there.
  assert.match(html, /title="Delete"/);
  assert.match(html, /<option[^>]*value="window"/);
  assert.match(html, /<span class="text-gray-400 truncate">pump<\/span>/, "the equipment type");
  assert.match(html, />roof</, "SPACE_LOC as a label");
});

test("rows carry their effective include state and dim when not built; the checkbox is the own flag", () => {
  setView("flat");
  const html = list();
  assert.equal(rowState(html, "deckA"), "included");
  assert.equal(rowState(html, "tank"), "excluded");
  assert.equal(rowState(html, "leg"), "excluded-by-structure", "the Jacket is left out");
  assert.match(html, /aria-label="Include tank in build"[^>]*aria-checked="false"/);
  assert.match(html, /aria-label="Include leg in build"[^>]*aria-checked="true"/, "its own flag stays true");
  assert.match(html, /title="Left out of the build with its structure"/);
  assert.match(html, /title="Left out of the build \(INCLUDE = false\)"/);
});

test("by structure: a row per structure with its count and its own INCLUDE mark; No structure gathers the rest", () => {
  setView("structure");
  const html = list();
  assert.deepEqual(groupsOf(html), ["structure:Topside=on", "structure:Jacket=off", "none:=on"]);
  assert.match(html, />Topside<\/span><span class="text-gray-400">\(4\)</);
  assert.match(html, />Jacket<\/span><span class="text-gray-400">\(1\)</);
  assert.match(html, />No structure<\/span><span class="text-gray-400">\(1\)</);
  assert.match(html, /aria-label="Include structure Jacket in build"[^>]*aria-checked="false"/);
  assert.match(html, /aria-label="Frame Topside"/);
  assert.match(html, /aria-expanded="true"/);
  assert.equal(rows(html), 6, "every component is listed under a structure");
});

test("by structure and cell: structure -> space -> its equipment and openings; mixed where they differ", () => {
  setView("cell");
  const html = list();
  const deckA = Object.values(useCellBuilderStore.getState().cells).find((c) => c.name === "deckA")!.id;
  assert.deepEqual(groupsOf(html), [
    "structure:Topside=on",
    `cell:${deckA}=mixed`,
    "structure:Jacket=off",
    `cell:${Object.values(useCellBuilderStore.getState().cells).find((c) => c.name === "leg")!.id}=on`,
    "none:=on",
    "unassigned:=on",
  ]);
  assert.match(html, /aria-label="Include space deckA in build"[^>]*aria-checked="mixed"/);
  assert.match(html, />deckA<\/span><span class="text-gray-400">\(4\)</, "the space and the three things in it");
  assert.match(html, />Unassigned<\/span><span class="text-gray-400">\(1\)</);
  // Depth shows as indentation: the pump sits two levels in.
  assert.match(html, /data-component-row="[^"]+" data-include-state="included"[^>]*padding-left:26px/);
  assert.equal(rows(html), 4, "spaces are group rows here, the other four are items");
});

test("a folded group row hides its children; the choice of view is remembered in this browser", () => {
  setView("structure");
  fold("structure:Jacket", true);
  const html = list();
  assert.match(html, /aria-label="Expand Jacket"/);
  assert.equal(rows(html), 5, "the leg is folded away");
  fold("structure:Jacket", false);
  assert.equal(rows(list()), 6);
  assert.equal(globalThis.localStorage.getItem("ada:model-panel-view"), "structure");
  setView("flat");
  assert.equal(globalThis.localStorage.getItem("ada:model-panel-view"), null, "the default leaves no key");
});

test("the panel body carries the count and the View option in its header", () => {
  const html = render(React.createElement(ComponentListBody));
  assert.match(html, /data-component-list-panel=""/);
  assert.match(html, />6 components</);
  assert.equal((html.match(/aria-label="Model view"/g) ?? []).length, 1, "once, in the header");
});
