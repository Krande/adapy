// Default names for a newly placed space/equipment/opening: "Space_01", "Equipment_01",
// "OPENING_01" -- unique, and reusing a name freed by a delete rather than growing without
// bound. See utils/cellbuilder/names.ts and state/cellbuilder/placementSlice.ts.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { nextFreeName, equipmentNameStem } = await import("@/utils/cellbuilder/names");

function model() {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: {},
    groups: [],
    loftMembers: [],
    activeGroup: null,
    selection: null,
    selectedCellIds: [],
    selectedEquipmentType: null,
    selectedCellType: null,
    cellTypes: [],
    openingTypes: [],
  } as never);
}
const st = () => useCellBuilderStore.getState();
const latest = (kind: string) => {
  const ids = Object.keys(st().cells).filter((id) => st().cells[id].kind === kind);
  return st().cells[ids[ids.length - 1]];
};

test("equipmentNameStem: Equipment for no type and for the generic box archetype; the type name otherwise", () => {
  assert.equal(equipmentNameStem(undefined), "Equipment");
  assert.equal(equipmentNameStem("box"), "Equipment");
  assert.equal(equipmentNameStem("BOX"), "Equipment");
  assert.equal(equipmentNameStem("pump"), "PUMP");
});

test("nextFreeName: the first free NN, zero-padded, reusing a gap", () => {
  assert.equal(nextFreeName("Space", {}), "Space_01");
  assert.equal(nextFreeName("Space", { a: { name: "Space_01" } }), "Space_02");
  assert.equal(
    nextFreeName("Space", { a: { name: "Space_01" }, b: { name: "Space_03" } }),
    "Space_02",
    "a gap (Space_02 deleted) is reused before growing past the highest number",
  );
  assert.equal(nextFreeName("Space", [{ name: "Space_01" }]), "Space_02", "also takes an array");
});

test("addCell: a new space is Space_01, the next Space_02", () => {
  model();
  st().addCell("cell", [0, 0, 0], [1, 1, 1], null);
  assert.equal(latest("cell").name, "Space_01");
  st().addCell("cell", [2, 0, 0], [1, 1, 1], null);
  assert.equal(latest("cell").name, "Space_02");
});

test("addCell: deleting Space_01 and adding one gives Space_01 again, never a duplicate", () => {
  model();
  st().addCell("cell", [0, 0, 0], [1, 1, 1], null);
  const first = latest("cell");
  assert.equal(first.name, "Space_01");
  st().addCell("cell", [2, 0, 0], [1, 1, 1], null);
  assert.equal(latest("cell").name, "Space_02");
  st().removeCell(first.id);
  st().addCell("cell", [4, 0, 0], [1, 1, 1], null);
  const names = Object.values(st().cells).map((c) => c.name).sort();
  assert.deepEqual(names, ["Space_01", "Space_02"], "Space_01 came back, no duplicate");
});

test("addCell: a box (or untyped) equipment is Equipment_01; a catalogue archetype keeps its type name", () => {
  model();
  st().addCell("equipment", [0, 0, 0], [1, 1, 1], null);
  assert.equal(latest("equipment").name, "Equipment_01");

  useCellBuilderStore.setState({ selectedEquipmentType: "box" } as never);
  st().addCell("equipment", [2, 0, 0], [1, 1, 1], null);
  assert.equal(latest("equipment").name, "Equipment_02", "box is the same archetype as untyped");

  useCellBuilderStore.setState({ selectedEquipmentType: "pump" } as never);
  st().addCell("equipment", [4, 0, 0], [1, 1, 1], null);
  assert.equal(latest("equipment").name, "PUMP_01", "a named archetype still counts separately");
});

test("addCell: an opening is still OPENING_NN, unique across deletes", () => {
  model();
  st().addCell("opening", [0, 0, 0], [1, 1, 1], null);
  assert.equal(latest("opening").name, "OPENING_01");
  st().addCell("opening", [2, 0, 0], [1, 1, 1], null);
  assert.equal(latest("opening").name, "OPENING_02");
});
