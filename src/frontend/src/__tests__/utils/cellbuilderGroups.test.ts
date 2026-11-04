import assert from "node:assert/strict";
import { test } from "node:test";

import {
  type CellGroup,
  GROUP_PALETTE,
  groupAfterRemoval,
  groupColor,
  groupFromWire,
  groupToStructureName,
  groupToWire,
  isGroupIncluded,
  normalizeGroups,
  objectGroup,
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

// ── include: build inclusion on the wire ────────────────────────────

test("include is written only when false (a built group stays byte-identical)", () => {
  assert.deepEqual(groupToWire({ name: "A", blueprint: "Framework" }), { name: "A", blueprint: "Framework" });
  assert.deepEqual(groupToWire({ name: "A", blueprint: "Framework", include: true }), {
    name: "A",
    blueprint: "Framework",
  });
  assert.deepEqual(groupToWire({ name: "A", blueprint: "Framework", include: false }), {
    name: "A",
    blueprint: "Framework",
    include: false,
  });
  assert.equal(JSON.stringify(normalizeGroups([{ name: "A", blueprint: "F", include: true }])), '[{"name":"A","blueprint":"F"}]');
});

test("groupFromWire: only an explicit false excludes", () => {
  assert.deepEqual(groupFromWire({ name: "A", blueprint: "F" }), { name: "A", blueprint: "F" });
  assert.deepEqual(groupFromWire({ name: "A", blueprint: "F", include: true }), { name: "A", blueprint: "F" });
  assert.deepEqual(groupFromWire({ name: "A", blueprint: "F", include: "false" }), { name: "A", blueprint: "F" });
  assert.deepEqual(groupFromWire({ name: "A", blueprint: "F", include: false }), {
    name: "A",
    blueprint: "F",
    include: false,
  });
  assert.equal(isGroupIncluded({}), true);
  assert.equal(isGroupIncluded({ include: false }), false);
  assert.equal(isGroupIncluded(undefined), true);
});

test("groups round-trip doc -> store -> doc keeps include", () => {
  // What a doc carries (the Mini Topside With Jacket template, jacket excluded),
  // read the way groupsFromDoc does, written the way toDoc does.
  const wire = [
    { name: "Mini", blueprint: "Stiffened plate" },
    { name: "Mini_jacket", blueprint: "Framework", include: false },
  ];
  const store = normalizeGroups(wire.map(groupFromWire));
  assert.deepEqual(store.map(isGroupIncluded), [true, false]);
  assert.deepEqual(JSON.parse(JSON.stringify(normalizeGroups(store))), wire);
  // Switching it back on drops the key again.
  const back = normalizeGroups(store.map((g) => ({ name: g.name, blueprint: g.blueprint })));
  assert.deepEqual(back, [
    { name: "Mini", blueprint: "Stiffened plate" },
    { name: "Mini_jacket", blueprint: "Framework" },
  ]);
});

// ── colour + membership for the group display ───────────────────────

test("groupColor: palette by position, override wins, null for a non-group", () => {
  const groups: CellGroup[] = [
    { name: "A", blueprint: "x" },
    { name: "B", blueprint: "x" },
  ];
  assert.equal(groupColor(groups, "A"), GROUP_PALETTE[0]);
  assert.equal(groupColor(groups, "B"), GROUP_PALETTE[1]);
  assert.equal(groupColor(groups, "B", { B: "#123456" }), "#123456");
  assert.equal(groupColor(groups, "nope"), null);
  assert.equal(groupColor(groups, undefined), null);
  assert.notEqual(groupColor(groups, "A"), groupColor(groups, "B"));
});

test("objectGroup: a cell is its own group; equipment takes the cell it sits in or on", () => {
  const lower = { kind: "cell", group: "Low", origin: [0, 0, 0], size: [10, 10, 3] };
  const upper = { kind: "cell", group: "High", origin: [0, 0, 3], size: [10, 10, 3] };
  const other = { kind: "cell", group: undefined, origin: [20, 0, 0], size: [5, 5, 3] };
  const cells = [lower, upper, other];
  assert.equal(objectGroup(lower, cells), "Low");
  // inside the upper deck
  assert.equal(objectGroup({ kind: "equipment", origin: [1, 1, 3], size: [1, 1, 1] }, cells), "High");
  // seated on the upper deck's roof
  assert.equal(objectGroup({ kind: "equipment", origin: [1, 1, 6], size: [1, 1, 1] }, cells), "High");
  // inside the lower one
  assert.equal(objectGroup({ kind: "equipment", origin: [1, 1, 0], size: [1, 1, 1] }, cells), "Low");
  // in an ungrouped cell, or outside every cell
  assert.equal(objectGroup({ kind: "opening", origin: [21, 1, 0], size: [1, 1, 1] }, cells), undefined);
  assert.equal(objectGroup({ kind: "equipment", origin: [50, 50, 0], size: [1, 1, 1] }, cells), undefined);
});

test("removeGroup unassigns exactly the deleted group's cells", () => {
  const removed = new Set(["Deck A"]);
  assert.equal(groupAfterRemoval("Deck A", removed), undefined); // cleared
  assert.equal(groupAfterRemoval("Deck B", removed), "Deck B"); // untouched
  assert.equal(groupAfterRemoval(undefined, removed), undefined);
});
