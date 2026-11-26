// A structure's ORIGIN (`CellGroup.origin`): where the structure sits, its items stored relative to
// it. It round-trips through the doc -- written whenever the group has one, [0, 0, 0] too, so an
// engine that saves it back can tell "moved to zero" from "a viewer that knows no origins" -- it
// survives the other group edits, moving it is one undoable edit, and an item's membership (the
// space it is in) is looked up only among its own structure's spaces: the same numbers in another
// structure are another place.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { containingCellName, placedCellsFromDoc } = await import("@/state/cellbuilder/docCodec");
const { containingCell, groupFromWire, groupToWire, normalizeGroups, structureOrigin } = await import("@/utils/cellbuilder/groups");
const { itemOrigin, itemStructure, placedCells } = await import("@/utils/cellbuilder/include");
type BuilderCell = import("@/state/cellbuilder/types").BuilderCell;

const st = () => useCellBuilderStore.getState();

// Two structures, each with a space at the same (relative) numbers: one at the model's origin, one
// 100 m along X. A third without an origin of its own.
const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "A1", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, STRUCTURE_NAME: "A" },
    { NAME: "B1", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, STRUCTURE_NAME: "B" },
  ],
  equipments: [{ NAME: "P1", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1, GLOBAL_COORDS: true, STRUCTURE_NAME: "B" }],
  openings: [],
  groups: [
    { name: "A", blueprint: "bp", origin: [0, 0, 0] },
    { name: "B", blueprint: "bp", origin: [100, 0, 5] },
    { name: "C", blueprint: "bp" },
  ],
} as unknown as ProceduralDoc;

const cellNamed = (name: string): BuilderCell => Object.values(st().cells).find((c) => c.name === name)!;

test("the wire: origin written whenever the group has one, [0, 0, 0] too; none stays none", () => {
  assert.deepEqual(groupToWire({ name: "A", blueprint: "bp", origin: [0, 0, 0] }), { name: "A", blueprint: "bp", origin: [0, 0, 0] });
  assert.deepEqual(groupToWire({ name: "A", blueprint: "bp" }), { name: "A", blueprint: "bp" });
  assert.deepEqual(groupToWire({ name: "A", blueprint: "bp", include: false, origin: [1, 2, 3] }), {
    name: "A",
    blueprint: "bp",
    include: false,
    origin: [1, 2, 3],
  });
  assert.deepEqual(groupFromWire({ name: "A", blueprint: "bp", origin: [1, "2", 3] }), { name: "A", blueprint: "bp", origin: [1, 2, 3] });
  // Not three numbers: no origin (the group sits at [0, 0, 0]).
  for (const origin of [[1, 2], [1, 2, "x"], "1,2,3", null])
    assert.equal("origin" in groupFromWire({ name: "A", blueprint: "bp", origin }), false, JSON.stringify(origin));
  assert.deepEqual(normalizeGroups([{ name: " A ", blueprint: "bp", origin: [0, 0, 0] }]), [{ name: "A", blueprint: "bp", origin: [0, 0, 0] }]);
  assert.deepEqual(structureOrigin([{ name: "B", blueprint: "bp", origin: [100, 0, 5] }], "B"), [100, 0, 5]);
  for (const name of [undefined, "*", "nope"]) assert.deepEqual(structureOrigin([{ name: "B", blueprint: "bp", origin: [1, 1, 1] }], name), [0, 0, 0]);
});

test("open -> toDoc keeps each group's origin and the items' relative coordinates", () => {
  st().loadFromDoc(doc);
  const out = st().toDoc();
  assert.deepEqual(out.groups, doc.groups);
  assert.deepEqual(
    (out.spaces ?? []).map((s) => [s.NAME, s.X, s.Y, s.Z]),
    [["A1", 0, 0, 0], ["B1", 0, 0, 0]],
  );
  const p1 = (out.equipments ?? [])[0];
  assert.deepEqual([p1.X, p1.Y, p1.Z, p1.STRUCTURE_NAME], [1, 1, 0, "B"]);
  // Its SPACE_NAME is the space of its own structure, not the first one at those numbers.
  assert.equal(p1.SPACE_NAME, "B1");
});

test("setGroupIncluded and renameGroup keep the origin; a rename repoints the items that name the structure", () => {
  st().loadFromDoc(doc);
  st().setGroupIncluded("B", false);
  assert.deepEqual(st().groups.find((g) => g.name === "B"), { name: "B", blueprint: "bp", include: false, origin: [100, 0, 5] });
  st().setGroupIncluded("B", true);
  assert.deepEqual(st().groups.find((g) => g.name === "B"), { name: "B", blueprint: "bp", origin: [100, 0, 5] });
  st().renameGroup("B", "B2");
  assert.deepEqual(st().groups.find((g) => g.name === "B2")?.origin, [100, 0, 5]);
  assert.equal(cellNamed("B1").group, "B2");
  assert.equal(cellNamed("P1").params.STRUCTURE_NAME, "B2", "the equipment follows its structure's new name");
  assert.deepEqual(itemOrigin(cellNamed("P1"), st().cells, st().groups), [100, 0, 5]);
  st().removeGroup("B2");
  assert.equal("STRUCTURE_NAME" in cellNamed("P1").params, false, "a removed structure is named by none");
});

test("setGroupOrigin moves a structure: one undoable, dirtying edit; [0, 0, 0] is still written", () => {
  st().loadFromDoc(doc);
  assert.equal(st().dirty, false);
  st().setGroupOrigin("A", [10, 20, 30]);
  assert.deepEqual(st().groups.find((g) => g.name === "A")?.origin, [10, 20, 30]);
  assert.equal(st().dirty, true);
  // The items keep their stored (relative) numbers; where they are follows.
  assert.deepEqual(cellNamed("A1").origin, [0, 0, 0]);
  assert.deepEqual(itemOrigin(cellNamed("A1"), st().cells, st().groups), [10, 20, 30]);
  // The same value again is no edit.
  const steps = st().past.length;
  st().setGroupOrigin("A", [10, 20, 30]);
  assert.equal(st().past.length, steps);
  st().undo();
  assert.deepEqual(st().groups.find((g) => g.name === "A")?.origin, [0, 0, 0]);
  st().redo();
  assert.deepEqual(st().groups.find((g) => g.name === "A")?.origin, [10, 20, 30]);
  // A group without an origin gets one; moved back to zero it is written as [0, 0, 0].
  st().setGroupOrigin("C", [0, 0, 0]);
  st().setGroupOrigin("A", [0, 0, 0]);
  const wire = st().toDoc().groups ?? [];
  assert.deepEqual(wire.find((g) => g.name === "A")?.origin, [0, 0, 0]);
  assert.deepEqual(wire.find((g) => g.name === "C")?.origin, [0, 0, 0]);
  // An unknown structure, or a value that is no number: nothing.
  const before = st().groups;
  st().setGroupOrigin("nope", [1, 1, 1]);
  st().setGroupOrigin("A", [NaN, 0, 0]);
  assert.equal(st().groups, before);
});

test("membership: an item is looked up only among its own structure's spaces", () => {
  const a1 = { id: "a1", name: "A1", kind: "cell", group: "A", origin: [0, 0, 0], size: [4, 4, 3], params: {} } as BuilderCell;
  const b1 = { id: "b1", name: "B1", kind: "cell", group: "B", origin: [0, 0, 0], size: [4, 4, 3], params: {} } as BuilderCell;
  const inB = { id: "e1", name: "E1", kind: "equipment", origin: [1, 1, 0], size: [1, 1, 1], params: { STRUCTURE_NAME: "B" } } as BuilderCell;
  const unnamed = { ...inB, id: "e2", params: {} } as BuilderCell;
  const cells = { a1, b1, e1: inB, e2: unnamed };
  const groups = doc.groups as never;
  assert.equal(containingCell(inB, [a1, b1])?.id, "b1");
  assert.equal(containingCellName(cells, inB), "B1");
  assert.equal(itemStructure(inB, cells, groups), "B");
  // One that names none keeps the geometric reading (the first space under it).
  assert.equal(containingCell(unnamed, [a1, b1])?.id, "a1");
  // Where things are in the model: each box moved by its structure's origin.
  const placed = placedCells(cells, groups);
  assert.deepEqual(placed.b1.origin, [100, 0, 5]);
  assert.deepEqual(placed.e1.origin, [101, 1, 5]);
  assert.deepEqual(placed.a1.origin, [0, 0, 0]);
  assert.deepEqual(cells.b1.origin, [0, 0, 0], "the stored boxes are left as they are");
  // With no origin anywhere it is the same map.
  assert.equal(placedCells(cells, [{ name: "A", blueprint: "bp" }]), cells);
  // A companion is drawn from its doc's placed cells.
  assert.deepEqual(placedCellsFromDoc(doc).find((c) => c.name === "B1")?.origin, [100, 0, 5]);
});

test("a new equipment or opening names the structure whose frame it is placed in", () => {
  st().loadFromDoc(doc);
  st().setActiveGroup("B");
  st().addCell("equipment", [1, 1, 0], [1, 1, 1]);
  const eq = st().cells[st().selection!.cellId!];
  assert.equal(eq.params.STRUCTURE_NAME, "B");
  st().addCell("opening", [1, 1, 0], [1, 1, 1], null);
  assert.equal("STRUCTURE_NAME" in st().cells[st().selection!.cellId!].params, false, "null: in none");
  st().addCell("opening", [1, 1, 0], [1, 1, 1], "A");
  assert.equal(st().cells[st().selection!.cellId!].params.STRUCTURE_NAME, "A");
  // Into a space: its structure's, whatever the active one is.
  st().insertOpeningOnFace(cellNamed("A1").id, 0, "door");
  assert.equal(st().cells[st().selection!.cellId!].params.STRUCTURE_NAME, "A");
  // Grown off a face: the source space's structure.
  st().extendCellFromFace(cellNamed("A1").id, 0, 2);
  assert.equal(st().cells[st().selection!.cellId!].group, "A");
});
