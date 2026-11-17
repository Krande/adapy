// A procedural model's source workbook in Storage: found by name, shown on the model's row.

import assert from "node:assert/strict";
import { test } from "node:test";

import { sourceLabel, sourceWorkbookKey, sourceWorkbooks } from "@/utils/storage/modelSource";

const keys = ["hvdc/hvdc.xlsx", "hvdc/hvdc.glb", "hvdc/fem/deck.SIN", "other/Other.XLSM", "loose.xlsx"];

test("the source is <model name>.xlsx (or .xlsm), whatever the case", () => {
  assert.equal(sourceWorkbookKey("hvdc/hvdc", keys), "hvdc/hvdc.xlsx");
  assert.equal(sourceWorkbookKey("other/other", keys), "other/Other.XLSM");
  assert.equal(sourceWorkbookKey("hvdc/fem/deck", keys), null, "a deck is not a workbook");
  assert.equal(sourceWorkbookKey("pm/mini", keys), null);
});

test("the models' sources, for those that have one", () => {
  const m = sourceWorkbooks(["hvdc/hvdc", "pm/mini", "other/other"], keys);
  assert.deepEqual([...m], [["hvdc/hvdc", "hvdc/hvdc.xlsx"], ["other/other", "other/Other.XLSM"]]);
});

test("a source shows under its file name", () => {
  assert.equal(sourceLabel("hvdc/hvdc.xlsx"), "hvdc.xlsx");
  assert.equal(sourceLabel("loose.xlsx"), "loose.xlsx");
});
