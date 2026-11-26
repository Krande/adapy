// The Location select (FLOOR/ROOF) must move the equipment, not just relabel it. The
// formula (z = newSurface + (oldZ - oldSurface), surface = space.Z for FLOOR, space.Z + space.DZ
// for ROOF) keeps the equipment's offset above its own seating surface, matching docCodec.ts's
// equipmentSpaceOffset (which derives the same world Z the other way, from the doc).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

const st = () => useCellBuilderStore.getState();

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "deckA", X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 3 }],
  equipments: [{ NAME: "pump", SPACE_NAME: "deckA", SPACE_LOC: "FLOOR", X: 1, Y: 1, Z: 0.2, LX: 1, LY: 1, LZ: 0.5 }],
} as unknown as ProceduralDoc;

function withModel(run: () => void): void {
  st().open("m", "Model", 1, doc);
  try {
    run();
  } finally {
    st().close();
  }
}

const pump = () => Object.values(st().cells).find((c) => c.name === "pump")!;

test("FLOOR -> ROOF lifts the equipment by the space's height, keeping X/Y and SPACE_LOC updated", () => {
  withModel(() => {
    const before = pump();
    const [x0, y0, z0] = before.origin;
    assert.equal(before.params.SPACE_LOC, "FLOOR");
    st().setEquipmentLocation(before.id, "ROOF");
    const after = pump();
    assert.equal(after.params.SPACE_LOC, "ROOF");
    assert.deepEqual([after.origin[0], after.origin[1]], [x0, y0], "X/Y unchanged");
    assert.ok(Math.abs(after.origin[2] - (z0 + 3)) < 1e-9, `z ${after.origin[2]} vs ${z0 + 3} (space DZ=3)`);
  });
});

test("ROOF -> FLOOR -> FLOOR is the identity (round trip, then a no-op)", () => {
  withModel(() => {
    const id = pump().id;
    const z0 = pump().origin[2];
    st().setEquipmentLocation(id, "ROOF");
    st().setEquipmentLocation(id, "FLOOR");
    assert.ok(Math.abs(pump().origin[2] - z0) < 1e-9, "back to where it started");
    assert.equal(pump().params.SPACE_LOC, "FLOOR");

    const past = st().past.length;
    st().setEquipmentLocation(id, "FLOOR"); // already FLOOR: no-op
    assert.equal(st().past.length, past, "no undo step for a no-op");
  });
});

test("without a host space, only SPACE_LOC changes (today's behaviour, preserved)", () => {
  st().open("m", "Model", 1, { ...doc, equipments: [{ NAME: "pump", SPACE_NAME: "NoSuchSpace", SPACE_LOC: "FLOOR", X: 0, Y: 0, Z: 5, LX: 1, LY: 1, LZ: 1 }] } as unknown as ProceduralDoc);
  try {
    const before = pump();
    const z0 = before.origin[2];
    st().setEquipmentLocation(before.id, "ROOF");
    assert.equal(pump().params.SPACE_LOC, "ROOF");
    assert.equal(pump().origin[2], z0, "origin is untouched -- no space to measure a surface from");
  } finally {
    st().close();
  }
});

test("one undo step restores both the origin and SPACE_LOC together", () => {
  withModel(() => {
    const id = pump().id;
    const z0 = pump().origin[2];
    const past = st().past.length;
    st().setEquipmentLocation(id, "ROOF");
    assert.equal(st().past.length, past + 1);
    st().undo();
    assert.equal(pump().params.SPACE_LOC, "FLOOR");
    assert.ok(Math.abs(pump().origin[2] - z0) < 1e-9);
  });
});

// Two spaces stacked at the same X/Y footprint but different Z (a mezzanine deck under a
// roof deck) -- the equipment has no SPACE_NAME of its own, so the host must be found by where it
// actually sits, not by which space happens to iterate first. "upper" is listed FIRST here
// deliberately: the old fallback (containingCellName, "the first space anywhere" among X/Y
// matches) would have picked it regardless of Z, even though the equipment sits on "lower"'s floor.
const stackedDoc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "upper", X: 0, Y: 0, Z: 3, DX: 10, DY: 10, DZ: 3 },
    { NAME: "lower", X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 3 },
  ],
  // GLOBAL_COORDS-equivalent: no SPACE_NAME, so X/Y/Z are already absolute world coords
  // (equipmentSpaceOffset, docCodec.ts) -- Z=0.2 sits just above "lower"'s floor (Z=0).
  equipments: [{ NAME: "pump", SPACE_LOC: "FLOOR", X: 1, Y: 1, Z: 0.2, LX: 1, LY: 1, LZ: 0.5 }],
} as unknown as ProceduralDoc;

test("with no SPACE_NAME, a stacked pair of spaces resolves to the one the equipment actually sits on, not the first one found", () => {
  st().open("m", "Model", 1, stackedDoc);
  try {
    const before = pump();
    assert.equal(before.origin[2], 0.2, "sitting just above lower's floor (Z=0)");
    st().setEquipmentLocation(before.id, "ROOF");
    const after = pump();
    assert.equal(after.params.SPACE_LOC, "ROOF");
    // lower's roof is Z=3 (DZ=3): offset above the old floor (0.2 - 0) preserved -> 3.2, not
    // upper's roof (Z=6) or any offset measured off the wrong space.
    assert.ok(Math.abs(after.origin[2] - 3.2) < 1e-9, `z ${after.origin[2]} vs 3.2 (lower's roof + the 0.2 offset)`);
  } finally {
    st().close();
  }
});

test("with no SPACE_NAME, the other member of a stacked pair resolves correctly too (seated on upper's floor)", () => {
  st().open("m", "Model", 1, {
    ...stackedDoc,
    equipments: [{ NAME: "pump", SPACE_LOC: "FLOOR", X: 1, Y: 1, Z: 3.2, LX: 1, LY: 1, LZ: 0.5 }],
  } as unknown as ProceduralDoc);
  try {
    const before = pump();
    assert.equal(before.origin[2], 3.2, "sitting just above upper's floor (Z=3)");
    st().setEquipmentLocation(before.id, "ROOF");
    const after = pump();
    assert.equal(after.params.SPACE_LOC, "ROOF");
    // upper's roof is Z=6 (3 + DZ 3): the same 0.2 offset, now off upper, not lower.
    assert.ok(Math.abs(after.origin[2] - 6.2) < 1e-9, `z ${after.origin[2]} vs 6.2 (upper's roof + the 0.2 offset)`);
  } finally {
    st().close();
  }
});

// The Location select shows FLOOR for an unset SPACE_LOC (SelectionSection.tsx's own
// `?? "FLOOR"`), but toDoc used to default the written column to ROOF -- and picking FLOOR from
// the select, already showing FLOOR, was a no-op (oldLoc === loc, both defaulted), so there was no
// way to fix it from the UI either. Both defaults must now agree, and picking FLOOR must commit it.

test("toDoc: an equipment placed with no SPACE_LOC of its own (e.g. the toolbar's typed position) writes FLOOR, matching the shown default", () => {
  withModel(() => {
    // addCell (placementSlice): a toolbar-placed equipment gets no SPACE_LOC at all.
    st().addCell("equipment", [2, 2, 0], [1, 1, 1], null);
    const placed = Object.values(st().cells).find((c) => c.kind === "equipment" && c.name !== "pump")!;
    assert.equal("SPACE_LOC" in placed.params, false, "nothing set yet");
    const written = (st().toDoc().equipments ?? []).find((e) => e.NAME === placed.name)!;
    assert.equal(written.SPACE_LOC, "FLOOR", "written default must match the UI's shown default, not ROOF");
  });
});

test("setEquipmentLocation: picking FLOOR on an equipment with no stored SPACE_LOC commits it explicitly", () => {
  withModel(() => {
    st().addCell("equipment", [2, 2, 0], [1, 1, 1], null);
    const placed = Object.values(st().cells).find((c) => c.kind === "equipment" && c.name !== "pump")!;
    assert.equal(placed.params.SPACE_LOC, undefined, "unset, though the select shows FLOOR for it");
    st().setEquipmentLocation(placed.id, "FLOOR"); // the value already shown: must still write
    const after = Object.values(st().cells).find((c) => c.id === placed.id)!;
    assert.equal(after.params.SPACE_LOC, "FLOOR", "now explicit, not merely a read-time default");
    // Once explicit, picking the SAME value again really is a no-op (no extra undo step).
    const past = st().past.length;
    st().setEquipmentLocation(after.id, "FLOOR");
    assert.equal(st().past.length, past, "already explicit FLOOR: true no-op");
  });
});

test("a non-equipment cell, or an unknown id, is a no-op", () => {
  withModel(() => {
    const space = Object.values(st().cells).find((c) => c.kind === "cell")!;
    const before = st().cells;
    st().setEquipmentLocation(space.id, "ROOF");
    assert.equal(st().cells, before, "a space is not an equipment: untouched");
    st().setEquipmentLocation("no-such-id", "ROOF");
    assert.equal(st().cells, before);
  });
});
