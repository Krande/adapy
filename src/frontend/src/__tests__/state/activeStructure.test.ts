// The active structure: a new space goes into the structure last picked (a structure, or any of its
// items) or just added -- not into none, as every new space did, to be moved by hand afterwards.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { activeStructure } = await import("@/utils/cellbuilder/groups");

const box = { origin: [0, 0, 0] as [number, number, number], size: [2, 2, 2] as [number, number, number] };

function model() {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: {
      a1: { id: "a1", name: "A1", kind: "cell", group: "Hull", ...box },
      b1: { id: "b1", name: "B1", kind: "cell", group: "Topside", ...box },
      e1: { id: "e1", name: "E1", kind: "equipment", params: { SPACE_NAME: "B1" }, ...box },
    },
    groups: [
      { name: "Hull", blueprint: "bp" },
      { name: "Topside", blueprint: "bp" },
    ],
    loftMembers: [],
    activeGroup: null,
    selection: null,
    selectedCellIds: [],
  } as never);
}
const st = () => useCellBuilderStore.getState();
const newest = (kind: string) => {
  const ids = Object.keys(st().cells).filter((id) => !["a1", "b1", "e1"].includes(id) && st().cells[id].kind === kind);
  return st().cells[ids[ids.length - 1]];
};

test("no active structure: a new space goes in none, as before", () => {
  model();
  st().addCell("cell", [5, 0, 0], [1, 1, 1]);
  assert.equal(newest("cell").group, undefined);
});

test("picking a structure's item makes it active; a new space goes into it", () => {
  model();
  st().setSelection({ kind: "cell", cellId: "e1" });
  assert.equal(activeStructure(st()), "Topside", "equipment in B1 is in Topside");
  st().addCell("cell", [5, 0, 0], [1, 1, 1]);
  assert.equal(newest("cell").group, "Topside");
  st().setSelection({ kind: "cell", cellId: "a1" });
  st().addCell("cell", [9, 0, 0], [1, 1, 1]);
  assert.equal(newest("cell").group, "Hull");
});

test("equipment and openings take no structure of their own", () => {
  model();
  st().setActiveGroup("Hull");
  st().addCell("opening", [5, 0, 0], [1, 1, 1]);
  assert.equal(newest("opening").group, undefined);
});

test("New structure: it becomes active; rename follows it, delete and undo drop it", () => {
  model();
  const name = st().addGroup();
  assert.equal(activeStructure(st()), name);
  st().renameGroup(name, "Flare");
  assert.equal(activeStructure(st()), "Flare");
  st().removeGroup("Flare");
  assert.equal(activeStructure(st()), undefined);
  const again = st().addGroup();
  st().undo();
  assert.equal(activeStructure(st()), undefined, `${again} was undone`);
});

test("setActiveGroup refuses a name that is no structure", () => {
  model();
  st().setActiveGroup("Nope");
  assert.equal(st().activeGroup, null);
});
