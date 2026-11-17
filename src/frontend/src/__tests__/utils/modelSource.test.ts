// A procedural model's source workbook in Storage: found by name, shown on the model's row.

import assert from "node:assert/strict";
import { test } from "node:test";

import { sourceLabel, sourceWorkbookKey, sourceWorkbooks } from "@/utils/storage/modelSource";

const keys = ["t1/t1.xlsx", "t1/t1.glb", "t1/fem/deck.SIN", "other/Other.XLSM", "loose.xlsx"];

test("the source is <model name>.xlsx (or .xlsm), whatever the case", () => {
  assert.equal(sourceWorkbookKey("t1/t1", keys), "t1/t1.xlsx");
  assert.equal(sourceWorkbookKey("other/other", keys), "other/Other.XLSM");
  assert.equal(sourceWorkbookKey("t1/fem/deck", keys), null, "a deck is not a workbook");
  assert.equal(sourceWorkbookKey("pm/mini", keys), null);
});

test("the models' sources, for those that have one", () => {
  const m = sourceWorkbooks(["t1/t1", "pm/mini", "other/other"], keys);
  assert.deepEqual([...m], [["t1/t1", "t1/t1.xlsx"], ["other/other", "other/Other.XLSM"]]);
});

test("a source shows under its file name", () => {
  assert.equal(sourceLabel("t1/t1.xlsx"), "t1.xlsx");
  assert.equal(sourceLabel("loose.xlsx"), "loose.xlsx");
});
