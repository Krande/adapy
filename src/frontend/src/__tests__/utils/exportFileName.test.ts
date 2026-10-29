import assert from "node:assert/strict";
import test from "node:test";

import { exportFileName, groupExportLabel, safeFileNamePart } from "@/utils/exportFileName";

test("a saved name has no spaces and nothing a file system reads as a path", () => {
  assert.equal(safeFileNamePart("  PLATE 2 of FRAME /A1(3)-B "), "PLATE_2_of_FRAME_A1_3_-B");
  assert.equal(safeFileNamePart("../../x"), "x");
});

test("the prefixes come first, each made safe, empty ones left out", () => {
  assert.equal(exportFileName({ serverName: "Beam_1.ifc" }), "Beam_1.ifc");
  assert.equal(exportFileName({ serverName: "Beam_1.ifc", project: "ABC" }), "ABC_Beam_1.ifc");
  assert.equal(exportFileName({ serverName: "Beam_1.step", project: "ABC", customPrefix: "rev b" }), "ABC_rev_b_Beam_1.step");
  assert.equal(exportFileName({ serverName: "Beam_1.step", project: null, customPrefix: "  " }), "Beam_1.step");
});

test("the selection's name is optional; the prefixes alone, or `export`, name the file without it", () => {
  assert.equal(exportFileName({ serverName: "Beam_1.ifc", project: "ABC", customPrefix: "rev-b", includeName: false }), "ABC_rev-b.ifc");
  assert.equal(exportFileName({ serverName: "Beam_1.ifc", includeName: false }), "export.ifc");
});

test("a group is named after its first member and how many more", () => {
  assert.equal(groupExportLabel(["Beam 1"]), "Beam 1");
  assert.equal(groupExportLabel(["Beam 1", "Beam 2", "Plate 3"]), "Beam 1_and_2_more");
  assert.equal(groupExportLabel([]), "selection");
});
