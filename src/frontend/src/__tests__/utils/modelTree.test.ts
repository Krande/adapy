import assert from "node:assert/strict";
import { test } from "node:test";

import type { BuilderCell } from "@/state/cellbuilder/types";
import type { CellGroup } from "@/utils/cellbuilder/groups";
import { isItemIncluded, itemCell, itemIncludeState, itemStructure, withInclude } from "@/utils/cellbuilder/include";
import {
  buildModelTree,
  cascadeMark,
  type GroupNode,
  type ModelNode,
} from "@/utils/cellbuilder/modelTree";

// The Model panel's tree over a two-structure model, the way the viewer gets it from
// the workbook: spaces carry STRUCTURE_NAME (the cell's `group`), equipment and
// openings carry SPACE_NAME (and sometimes STRUCTURE_NAME) in their params.

const box = (
  id: string,
  kind: BuilderCell["kind"],
  origin: [number, number, number],
  size: [number, number, number],
  extra: Partial<BuilderCell> = {},
): BuilderCell => ({ id, name: id, kind, origin, size, params: {}, ...extra });

const groups: CellGroup[] = [
  { name: "Topside", blueprint: "steel_stru" },
  { name: "Jacket", blueprint: "jacket", include: false },
];

const cells: Record<string, BuilderCell> = Object.fromEntries(
  [
    box("deckA", "cell", [0, 0, 0], [10, 10, 4], { group: "Topside" }),
    box("deckB", "cell", [10, 0, 0], [10, 10, 4], { group: "Topside" }),
    box("leg", "cell", [0, 0, -30], [4, 4, 30], { group: "Jacket" }),
    box("loose", "cell", [50, 50, 0], [2, 2, 2]),
    // Named into deckA by SPACE_NAME, although it stands over deckB.
    box("pumpA", "equipment", [12, 2, 0], [1, 1, 1], { params: { SPACE_NAME: "deckA", SPACE_LOC: "FLOOR" } }),
    // No SPACE_NAME: placed in deckB by geometry.
    box("tankB", "equipment", [15, 5, 0], [2, 2, 2], { params: {} }),
    // Excluded itself, inside deckA.
    box("doorA", "opening", [2, 0, 0], [1, 0.2, 2], { params: { SPACE_NAME: "deckA", INCLUDE: false } }),
    // STRUCTURE_NAME says Jacket; sits in no space.
    box("clamp", "equipment", [60, 60, 0], [1, 1, 1], { params: { STRUCTURE_NAME: "Jacket" } }),
    // In no structure and in no space.
    box("buoy", "equipment", [-20, -20, 0], [1, 1, 1], { params: {} }),
  ].map((c) => [c.id, c]),
);

const labels = (nodes: ModelNode[]): string[] => nodes.map((n) => (n.type === "item" ? n.cell.id : `${n.label}(${n.itemIds.length})`));
const group = (nodes: ModelNode[], label: string): GroupNode => {
  const g = nodes.find((n) => n.type === "group" && n.label === label);
  assert.ok(g && g.type === "group", `group ${label}`);
  return g;
};

test("include: own flag, structure of each component, and the space it is placed in", () => {
  assert.equal(isItemIncluded(cells.deckA), true);
  assert.equal(isItemIncluded(cells.doorA), false);
  assert.equal(isItemIncluded({ kind: "loft", params: { INCLUDE: false } }), true, "a loft band that exists is built");
  assert.deepEqual(withInclude({ X: 1 }, false), { X: 1, INCLUDE: false });
  assert.deepEqual(withInclude({ X: 1, INCLUDE: false }, true), { X: 1 }, "included = no key, as the writer defaults it");

  assert.equal(itemCell(cells.pumpA, cells)?.id, "deckA", "SPACE_NAME wins over geometry");
  assert.equal(itemCell(cells.tankB, cells)?.id, "deckB", "geometry when SPACE_NAME is absent");
  assert.equal(itemCell(cells.clamp, cells), null);
  assert.equal(itemCell(cells.deckA, cells), null);

  assert.equal(itemStructure(cells.deckA, cells, groups), "Topside");
  assert.equal(itemStructure(cells.pumpA, cells, groups), "Topside", "through its space");
  assert.equal(itemStructure(cells.clamp, cells, groups), "Jacket", "its own STRUCTURE_NAME");
  assert.equal(itemStructure(cells.buoy, cells, groups), undefined);
  assert.equal(itemStructure(cells.loose, cells, groups), undefined);
  assert.equal(
    itemStructure({ ...cells.buoy, params: { STRUCTURE_NAME: "Nope" } }, cells, groups),
    undefined,
    "a STRUCTURE_NAME naming no structure counts for nothing",
  );

  assert.equal(itemIncludeState(cells.deckA, cells, groups), "included");
  assert.equal(itemIncludeState(cells.doorA, cells, groups), "excluded");
  assert.equal(itemIncludeState(cells.leg, cells, groups), "excluded-by-structure");
  assert.equal(itemIncludeState(cells.clamp, cells, groups), "excluded-by-structure");
  assert.equal(itemIncludeState({ ...cells.leg, params: { INCLUDE: false } }, cells, groups), "excluded", "its own flag is reported first");
});

test("flat: every component in document order, with its effective state", () => {
  const nodes = buildModelTree("flat", { cells, groups });
  assert.deepEqual(labels(nodes), Object.keys(cells));
  assert.ok(nodes.every((n) => n.type === "item" && n.depth === 0));
  const states = Object.fromEntries(nodes.map((n) => [n.type === "item" ? n.cell.id : "", n.state]));
  assert.equal(states.leg, "excluded-by-structure");
  assert.equal(states.doorA, "excluded");
  assert.equal(states.pumpA, "included");
});

test("the structure tree: structure -> space -> what is placed in it; Unassigned for the rest", () => {
  const nodes = buildModelTree("tree", { cells, groups });
  assert.deepEqual(labels(nodes), ["Topside(5)", "Jacket(2)", "No structure(2)"]);
  const topside = group(nodes, "Topside");
  assert.deepEqual(labels(topside.children), ["deckA(3)", "deckB(2)"]);
  const deckA = group(topside.children, "deckA");
  assert.equal(deckA.kind, "cell");
  assert.equal(deckA.cell?.id, "deckA");
  assert.deepEqual(deckA.itemIds, ["deckA", "pumpA", "doorA"], "the space itself and what is in it");
  assert.deepEqual(labels(deckA.children), ["pumpA", "doorA"]);
  assert.equal(deckA.include, "mixed", "the door is out, the rest in");
  assert.ok(deckA.children.every((n) => n.depth === 2));
  const deckB = group(topside.children, "deckB");
  assert.deepEqual(labels(deckB.children), ["tankB"]);
  assert.equal(deckB.include, "on");

  const jacket = group(nodes, "Jacket");
  assert.deepEqual(labels(jacket.children), ["leg(1)", "Unassigned(1)"]);
  const unassigned = group(jacket.children, "Unassigned");
  assert.equal(unassigned.kind, "unassigned");
  assert.deepEqual(labels(unassigned.children), ["clamp"]);
  assert.equal(unassigned.state, "excluded-by-structure");

  const none = group(nodes, "No structure");
  assert.deepEqual(labels(none.children), ["loose(1)", "Unassigned(1)"]);
});

test("with no structure defined the structure layer is left out", () => {
  const plain = Object.fromEntries(
    Object.values(cells)
      .filter((c) => c.id !== "clamp")
      .map((c) => [c.id, { ...c, group: undefined, params: { ...c.params, STRUCTURE_NAME: undefined } }]),
  );
  const tree = buildModelTree("tree", { cells: plain, groups: [] });
  assert.deepEqual(labels(tree), ["deckA(3)", "deckB(2)", "leg(1)", "loose(1)", "Unassigned(1)"]);
  assert.ok(tree.every((n) => n.depth === 0));
});

test("cascade marks: on, off, mixed", () => {
  const on = { kind: "cell" as const, params: {} };
  const off = { kind: "equipment" as const, params: { INCLUDE: false } };
  assert.equal(cascadeMark([on, on]), "on");
  assert.equal(cascadeMark([off, off]), "off");
  assert.equal(cascadeMark([on, off]), "mixed");
  assert.equal(cascadeMark([]), "on");
});

test("a loft band belongs to its member's structure", () => {
  const band = box("riser_bay0", "loft", [0, 0, 0], [1, 1, 5], {
    loft: { member: "riser", bay: 0, bandCount: 1, cellName: "riser_bay0", rings: [[], []], stationLo: { TYPE: "circle" }, stationHi: { TYPE: "circle" }, excludeFaces: [] } as never,
  });
  const members = [{ NAME: "riser", STRUCTURE_NAME: "Jacket" }];
  assert.equal(itemStructure(band, cells, groups, members), "Jacket");
  assert.equal(itemStructure(band, cells, groups, []), undefined);
  const nodes = buildModelTree("tree", { cells: { ...cells, riser_bay0: band }, groups, loftMembers: members });
  const jacket = group(nodes, "Jacket");
  assert.deepEqual(labels(group(jacket.children, "Unassigned").children), ["riser_bay0", "clamp"]);
});
