import assert from "node:assert/strict";
import { test } from "node:test";

import {
  type CellGroup,
  groupAfterRemoval,
  groupToStructureName,
  normalizeGroups,
  resolveCellGroup,
  structureNameToGroup,
} from "../../utils/cellbuilder/groups";

// These pure helpers ARE the group round-trip that the store's toDoc/cellsFromDoc
// delegate to: a cell's `group` <-> a space doc's `STRUCTURE_NAME`, plus the
// top-level `groups` list and removeGroup's cell unassignment.

test("groupToStructureName omits blank/undefined, trims non-blank", () => {
  assert.equal(groupToStructureName(undefined), undefined);
  assert.equal(groupToStructureName(null), undefined);
  assert.equal(groupToStructureName(""), undefined);
  assert.equal(groupToStructureName("  "), undefined);
  assert.equal(groupToStructureName("A"), "A");
  assert.equal(groupToStructureName("  A  "), "A");
});

test("structureNameToGroup reads STRUCTURE_NAME back, blank = ungrouped", () => {
  assert.equal(structureNameToGroup({ STRUCTURE_NAME: "A" }), "A");
  assert.equal(structureNameToGroup({ STRUCTURE_NAME: "  " }), undefined);
  assert.equal(structureNameToGroup({}), undefined);
  assert.equal(structureNameToGroup({ STRUCTURE_NAME: 3 }), undefined);
});

test("normalizeGroups drops blank + duplicate names, keeps order", () => {
  const groups: CellGroup[] = [
    { name: "A", blueprint: "Framework" },
    { name: "  ", blueprint: "x" },
    { name: "B", blueprint: "Stiffened plate" },
    { name: "A", blueprint: "dup-ignored" },
  ];
  assert.deepEqual(normalizeGroups(groups), [
    { name: "A", blueprint: "Framework" },
    { name: "B", blueprint: "Stiffened plate" },
  ]);
});

test("toDoc -> cellsFromDoc preserves groups + per-cell group", () => {
  // A model: two groups (distinct blueprints) + a mix of grouped/ungrouped cells.
  const groups: CellGroup[] = [
    { name: "Deck A", blueprint: "Framework with deck" },
    { name: "Deck B", blueprint: "Stiffened plate" },
  ];
  const cells = [
    { name: "c1", group: "Deck A" as string | undefined },
    { name: "c2", group: "Deck B" as string | undefined },
    { name: "c3", group: undefined },
  ];

  // toDoc side: stamp STRUCTURE_NAME (omit when ungrouped) + serialize groups.
  const spaceDocs = cells.map((c) => {
    const sn = groupToStructureName(c.group);
    return { NAME: c.name, ...(sn ? { STRUCTURE_NAME: sn } : {}) };
  });
  assert.equal("STRUCTURE_NAME" in spaceDocs[2], false); // ungrouped omits the key
  const docGroups = normalizeGroups(groups);

  // cellsFromDoc side: read each cell's group back, reconciled against the list.
  const roundTripped = spaceDocs.map((s) => ({
    name: String(s.NAME),
    group: resolveCellGroup(structureNameToGroup(s), docGroups),
  }));

  assert.deepEqual(roundTripped, cells);
  assert.deepEqual(docGroups, groups);
});

test("resolveCellGroup drops a group that is no longer defined", () => {
  const groups: CellGroup[] = [{ name: "A", blueprint: "Framework" }];
  assert.equal(resolveCellGroup("A", groups), "A");
  assert.equal(resolveCellGroup("gone", groups), undefined); // stale STRUCTURE_NAME
  assert.equal(resolveCellGroup(undefined, groups), undefined);
});

test("removeGroup unassigns exactly the deleted group's cells", () => {
  const removed = new Set(["Deck A"]);
  assert.equal(groupAfterRemoval("Deck A", removed), undefined); // cleared
  assert.equal(groupAfterRemoval("Deck B", removed), "Deck B"); // untouched
  assert.equal(groupAfterRemoval(undefined, removed), undefined);
});
