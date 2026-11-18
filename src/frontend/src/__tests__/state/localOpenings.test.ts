// A workbook places an opening on a side of its space (SPACE_NAME, SPACE_SIDE, POS_X/POS_Y,
// SIZE_X/SIZE_Y, DEPTH). The builder skipped those, held only its own (global) openings, and wrote
// back only those: a commit from Topology > Input dropped every workbook opening. Now they are drawn
// where ada builds them, edited as boxes, and written back in their local form; an opening the builder
// cannot place at all is kept as it came.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { cellsFromDoc, localOpeningBox } = await import("@/state/cellbuilder/docCodec");

const space = { NAME: "deck", X: 10, Y: 20, Z: 30, DX: 4, DY: 5, DZ: 3 };
const onSouth = { NAME: "door_s", SPACE_NAME: "deck", SPACE_SIDE: "-Y", POS_X: 1, POS_Y: 0, SIZE_X: 0.9, SIZE_Y: 2.1, DEPTH: 0.2, SUBTYPE: "door", INCLUDE: true };
const onRoof = { NAME: "hatch", SPACE_NAME: "deck", SPACE_SIDE: "Z", POS_X: 2, POS_Y: 3, SIZE_X: 1, SIZE_Y: 1, DEPTH: 0.4, INCLUDE: true };
const global = { NAME: "ui_door", SUBTYPE: "door", USE_GLOBAL_COORDS: true, X: 10, Y: 22, Z: 30, DX: 0.2, DY: 0.9, DZ: 2.1, INCLUDE: true };
const unplaced = { NAME: "lost", SPACE_NAME: "nowhere", SPACE_SIDE: "X", POS_X: 0, POS_Y: 0, SIZE_X: 1, SIZE_Y: 1, DEPTH: 0.2, INCLUDE: true };
const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [space],
  equipments: [],
  openings: [onSouth, onRoof, global, unplaced],
} as unknown as ProceduralDoc;

const st = () => useCellBuilderStore.getState();
const opening = (name: string) => Object.values(st().cells).find((c) => c.kind === "opening" && c.name === name)!;
const written = (name: string) => ((st().toDoc() as unknown as { openings: Record<string, unknown>[] }).openings).find((o) => o.NAME === name)!;

test("a workbook opening is drawn on its side, where ada builds it", () => {
  const box = localOpeningBox(onSouth, { origin: [10, 20, 30], size: [4, 5, 3] })!;
  // -Y: POS_X along X, POS_Y along Z, DEPTH through Y about the space's min Y.
  assert.deepEqual(box, { origin: [11, 19.9, 30], size: [0.9, 0.2, 2.1] });
  const roof = localOpeningBox(onRoof, { origin: [10, 20, 30], size: [4, 5, 3] })!;
  // Z: POS_X/POS_Y along X/Y, DEPTH through Z about the space's top.
  assert.deepEqual(roof, { origin: [12, 23, 32.8], size: [1, 1, 0.4] });
  const cells = Object.values(cellsFromDoc(doc)).filter((c) => c.kind === "opening");
  assert.deepEqual(cells.map((c) => c.name).sort(), ["door_s", "hatch", "ui_door"], "the unplaceable one is not drawn");
});

test("an edit and a commit keep every opening, each in its own form", () => {
  st().open("m", "Model", 1, doc);
  st().renameCell(Object.values(st().cells).find((c) => c.kind === "cell")!.id, "deck");
  const out = (st().toDoc() as unknown as { openings: Record<string, unknown>[] }).openings;
  assert.deepEqual(out.map((o) => o.NAME).sort(), ["door_s", "hatch", "lost", "ui_door"], "none dropped");
  const s = written("door_s");
  assert.equal(s.USE_GLOBAL_COORDS, false);
  assert.equal("X" in s || "DX" in s, false, "not converted to global");
  for (const k of ["SPACE_NAME", "SPACE_SIDE", "POS_X", "POS_Y", "SIZE_X", "SIZE_Y", "DEPTH"] as const)
    assert.ok(Math.abs(Number(s[k] ?? NaN) - Number(onSouth[k])) < 1e-9 || s[k] === onSouth[k], k);
  assert.deepEqual(written("lost"), unplaced, "the unplaceable one goes back as it came");
  assert.equal(written("ui_door").X, 10, "a builder opening stays global");
});

test("moving a workbook opening along its side moves its POS, in its local form", () => {
  st().open("m", "Model", 1, doc);
  const c = opening("door_s");
  st().updateCell(c.id, { origin: [c.origin[0] + 1.5, c.origin[1], c.origin[2] + 0.5] });
  const s = written("door_s");
  assert.ok(Math.abs(Number(s.POS_X) - 2.5) < 1e-9, String(s.POS_X));
  assert.ok(Math.abs(Number(s.POS_Y) - 0.5) < 1e-9, String(s.POS_Y));
  assert.equal(s.SPACE_SIDE, "-Y");
  assert.equal(s.USE_GLOBAL_COORDS, false);
});

test("the local columns are written without float noise", () => {
  st().open("m", "Model", 1, { ...doc, spaces: [{ ...space, X: 7.8 }], openings: [{ ...onRoof, POS_X: 2.6 }] } as unknown as ProceduralDoc);
  assert.equal(written("hatch").POS_X, 2.6, "10.4 - 7.8 is 2.6 here, not 2.5999999999999996");
});

test("renaming the space takes its workbook openings along, still in their local form", () => {
  st().open("m", "Model", 1, doc);
  const deck = Object.values(st().cells).find((c) => c.kind === "cell" && c.name === "deck")!;
  st().renameCell(deck.id, "main_deck");
  const s = written("door_s");
  assert.equal(s.SPACE_NAME, "main_deck");
  assert.equal(s.USE_GLOBAL_COORDS, false);
  assert.ok(Math.abs(Number(s.POS_X) - 1) < 1e-9, String(s.POS_X));
  assert.equal(written("lost").SPACE_NAME, "nowhere", "one on another space is left alone");
});

test("an arrow-key nudge along one axis leaves the other two as they were", () => {
  st().open("m", "Model", 1, doc);
  st().setGridStep(1);
  const c = opening("door_s");
  st().translateCellAlongAxis(c.id, 0, 1);
  const after = opening("door_s");
  assert.equal(after.origin[0], 12);
  assert.ok(Math.abs(after.origin[1] - c.origin[1]) < 1e-12 && Math.abs(after.origin[2] - c.origin[2]) < 1e-12);
});
