// An equipment's SPACE_NAME must stay the space it was SEATED in -- the one the insert
// popover put it into (placementSlice's insertEquipmentIntoCell) -- not "whichever space's X/Y
// footprint happens to hold its centre" (docCodec's containingCellName, the old sole source of
// truth every equipment was written against in toDoc). A space wrapping a smaller one head-on in
// plan (a deck's footprint covering a room under it) used to always win for every equipment
// above it, however it was actually seated.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";
import type { BuilderCell } from "@/state/cellbuilder/types";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { containingCellName } = await import("@/state/cellbuilder/docCodec");

const st = () => useCellBuilderStore.getState();

// "outer" covers the whole plan footprint of "inner" (and more); an equipment sitting over
// "inner" also sits, geometrically, over "outer" -- the old bug's exact shape.
const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "outer", X: 0, Y: 0, Z: 0, DX: 20, DY: 20, DZ: 1 },
    { NAME: "inner", X: 5, Y: 5, Z: 0, DX: 4, DY: 4, DZ: 3 },
  ],
  equipments: [],
} as unknown as ProceduralDoc;

function withModel(run: () => void): void {
  st().open("m", "Model", 1, doc);
  try {
    run();
  } finally {
    st().close();
  }
}

const cellByName = (name: string) => Object.values(st().cells).find((c) => c.name === name)!;

test("containingCellName: a named, resolvable seat wins outright, even though geometry would pick the outer space", () => {
  const outer = { id: "o", name: "outer", kind: "cell", origin: [0, 0, 0], size: [20, 20, 1], params: {} } as BuilderCell;
  const inner = { id: "i", name: "inner", kind: "cell", origin: [5, 5, 0], size: [4, 4, 3], params: {} } as BuilderCell;
  const eq = { id: "e", name: "eq", kind: "equipment", origin: [6, 6, 1], size: [1, 1, 1], params: { SPACE_NAME: "inner" } } as BuilderCell;
  const cells = { o: outer, i: inner, e: eq };
  assert.equal(containingCellName(cells, eq), "inner");
});

test("containingCellName: no seat named -- the old geometric fallback still applies", () => {
  const outer = { id: "o", name: "outer", kind: "cell", origin: [0, 0, 0], size: [20, 20, 1], params: {} } as BuilderCell;
  const inner = { id: "i", name: "inner", kind: "cell", origin: [5, 5, 0], size: [4, 4, 3], params: {} } as BuilderCell;
  const eq = { id: "e", name: "eq", kind: "equipment", origin: [6, 6, 1], size: [1, 1, 1], params: {} } as BuilderCell;
  const cells = { o: outer, i: inner, e: eq };
  assert.equal(containingCellName(cells, eq), "outer", "unnamed: the first space under it, as before");
});

test("containingCellName: a seat name that no longer resolves (its space deleted/renamed) falls back to geometry too", () => {
  const outer = { id: "o", name: "outer", kind: "cell", origin: [0, 0, 0], size: [20, 20, 1], params: {} } as BuilderCell;
  const eq = { id: "e", name: "eq", kind: "equipment", origin: [6, 6, 1], size: [1, 1, 1], params: { SPACE_NAME: "gone" } } as BuilderCell;
  const cells = { o: outer, e: eq };
  assert.equal(containingCellName(cells, eq), "outer");
});

test("containingCellName: a seat outside the equipment's own structure is ignored (limited to its own structure)", () => {
  const a = { id: "a", name: "spaceA", kind: "cell", group: "A", origin: [0, 0, 0], size: [20, 20, 1], params: {} } as BuilderCell;
  const b = { id: "b", name: "spaceB", kind: "cell", group: "B", origin: [5, 5, 0], size: [4, 4, 3], params: {} } as BuilderCell;
  // Names spaceB, but is itself in structure A: spaceB does not count as its seat.
  const eq = { id: "e", name: "eq", kind: "equipment", origin: [6, 6, 1], size: [1, 1, 1], params: { SPACE_NAME: "spaceB", STRUCTURE_NAME: "A" } } as BuilderCell;
  const cells = { a, b, e: eq };
  assert.equal(containingCellName(cells, eq), "spaceA");
});

test("insertEquipmentIntoCell: creating equipment on a cell via the insert popover stores that cell as its seat", () => {
  withModel(() => {
    const inner = cellByName("inner");
    st().insertEquipmentIntoCell({ equipmentId: null, cellId: inner.id, surface: "floor", side: "top" });
    const eq = Object.values(st().cells).find((c) => c.kind === "equipment")!;
    assert.equal(eq.params.SPACE_NAME, "inner");
    // And toDoc writes that seat, not the first (outer) space its geometry also sits over.
    const written = (st().toDoc().equipments ?? []).find((e) => e.NAME === eq.name)!;
    assert.equal(written.SPACE_NAME, "inner");
  });
});

test("insertEquipmentIntoCell: re-seating an existing equipment onto another cell updates its stored seat", () => {
  withModel(() => {
    const outer = cellByName("outer");
    const inner = cellByName("inner");
    st().insertEquipmentIntoCell({ equipmentId: null, cellId: outer.id, surface: "floor", side: "top" });
    const eq = Object.values(st().cells).find((c) => c.kind === "equipment")!;
    assert.equal(eq.params.SPACE_NAME, "outer");
    st().insertEquipmentIntoCell({ equipmentId: eq.id, cellId: inner.id, surface: "floor", side: "top" });
    const after = Object.values(st().cells).find((c) => c.kind === "equipment")!;
    assert.equal(after.params.SPACE_NAME, "inner");
    assert.equal((st().toDoc().equipments ?? []).find((e) => e.NAME === after.name)!.SPACE_NAME, "inner");
  });
});

test("insertEquipmentAtLocal: the keyboard insert's host cell is stored as the seat too", () => {
  withModel(() => {
    const inner = cellByName("inner");
    st().insertEquipmentAtLocal(inner.id, [1, 1]);
    const eq = Object.values(st().cells).find((c) => c.kind === "equipment")!;
    assert.equal(eq.params.SPACE_NAME, "inner");
  });
});
