// The blueprint colours the scene and the Model panel draw a structure's cells in, and
// which structure the Builder's selection puts in focus (the Compile settings' Blueprint).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { BuilderCell } from "@/state/cellbuilder/types";
import {
  BLUEPRINT_COLOURS,
  CELL_BLUE,
  EXTRA_BLUEPRINT_PALETTE,
  blueprintColour,
  blueprintsInUse,
  hexToNumber,
  structureColour,
} from "@/utils/cellbuilder/blueprintColours";
import type { CellGroup } from "@/utils/cellbuilder/groups";
import { focusedStructure } from "@/utils/cellbuilder/structureFocus";

const groups: CellGroup[] = [
  { name: "Topside", blueprint: "Stiffened plate" },
  { name: "Jacket", blueprint: "Framework" },
  { name: "Stubs", blueprint: "Framework" },
  { name: "Module", blueprint: "Framework with deck" },
  { name: "Odd", blueprint: "space_frame_v2" },
  { name: "Blank", blueprint: "" },
];

test("the named blueprints keep their colours; the Stiffened plate is the cell blue", () => {
  assert.equal(blueprintColour("Stiffened plate"), CELL_BLUE);
  assert.equal(blueprintColour("Framework"), BLUEPRINT_COLOURS.Framework);
  assert.equal(blueprintColour("Framework with deck"), BLUEPRINT_COLOURS["Framework with deck"]);
  assert.equal(new Set(Object.values(BLUEPRINT_COLOURS)).size, 3, "three blueprints, three colours");
  assert.equal(hexToNumber(CELL_BLUE), 0x3b82f6, "the scene's CELL_COLOR");
});

test("an unknown blueprint gets a stable colour from the extra palette; none gets the cell blue", () => {
  const c = blueprintColour("space_frame_v2");
  assert.ok(EXTRA_BLUEPRINT_PALETTE.includes(c));
  assert.equal(blueprintColour("space_frame_v2"), c, "stable");
  assert.equal(blueprintColour(""), CELL_BLUE);
  assert.equal(blueprintColour(null), CELL_BLUE);
  assert.equal(blueprintColour(undefined), CELL_BLUE);
});

test("a structure's colour is its blueprint's; a structure naming none, or none at all, is the cell blue", () => {
  assert.equal(structureColour(groups, "Jacket"), BLUEPRINT_COLOURS.Framework);
  assert.equal(structureColour(groups, "Blank"), CELL_BLUE);
  assert.equal(structureColour(groups, "Nowhere"), CELL_BLUE);
  assert.equal(structureColour(groups, undefined), CELL_BLUE);
});

test("the legend lists each blueprint in use once, in the structures' order, blanks left out", () => {
  assert.deepEqual(blueprintsInUse(groups), ["Stiffened plate", "Framework", "Framework with deck", "space_frame_v2"]);
  assert.deepEqual(blueprintsInUse([]), []);
});

const box = (id: string, kind: BuilderCell["kind"], origin: [number, number, number], size: [number, number, number], extra: Partial<BuilderCell> = {}): BuilderCell => ({
  id,
  name: id,
  kind,
  origin,
  size,
  params: {},
  ...extra,
});
const cells: Record<string, BuilderCell> = Object.fromEntries(
  [
    box("deckA", "cell", [0, 0, 0], [10, 10, 4], { group: "Topside" }),
    box("deckB", "cell", [10, 0, 0], [10, 10, 4], { group: "Topside" }),
    box("leg", "cell", [0, 0, -30], [4, 4, 30], { group: "Jacket" }),
    box("loose", "cell", [50, 50, 0], [2, 2, 2]),
    box("pumpA", "equipment", [1, 1, 0], [1, 1, 1]),
    box("buoy", "equipment", [-20, -20, 0], [1, 1, 1]),
  ].map((c) => [c.id, c]),
);

test("the structure in focus: the one every selected component belongs to", () => {
  const sel = (cellId: string) => ({ kind: "cell" as const, cellId });
  assert.equal(focusedStructure(sel("deckA"), ["deckA"], cells, groups), "Topside");
  assert.equal(focusedStructure(sel("pumpA"), ["pumpA"], cells, groups), "Topside", "a unit takes the structure of the space it sits in");
  assert.equal(focusedStructure(sel("deckA"), ["deckA", "deckB", "pumpA"], cells, groups), "Topside", "a structure row's selection");
  assert.equal(focusedStructure(sel("leg"), ["leg"], cells, groups), "Jacket");
});

test("no focus: nothing selected, a selection across structures, or one in no structure", () => {
  const sel = (cellId: string) => ({ kind: "cell" as const, cellId });
  assert.equal(focusedStructure(null, [], cells, groups), undefined);
  assert.equal(focusedStructure(sel("deckA"), ["deckA", "leg"], cells, groups), undefined);
  assert.equal(focusedStructure(sel("loose"), ["loose"], cells, groups), undefined);
  assert.equal(focusedStructure(sel("buoy"), ["buoy"], cells, groups), undefined);
  assert.equal(focusedStructure(sel("gone"), ["gone"], cells, groups), undefined, "an id no longer in the model");
});
