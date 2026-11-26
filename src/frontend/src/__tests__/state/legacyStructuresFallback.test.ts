// A doc that predates `groups[]` names its structures in a top-level `structures` list instead
// (an engine's own structure records: {NAME, X?, Y?, Z?, INCLUDE?}). groupsFromDoc (docCodec.ts)
// reads that into the SAME CellGroup shape when the doc carries no `groups` of its own, so the
// rest of the builder (origins, per-structure blueprint, include) never has to know which shape a
// doc arrived in. From the moment it is read, `structures` is a builder-owned key
// (BUILDER_DOC_KEYS): a commit writes `groups`, never both shapes.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { groupsFromDoc } = await import("@/state/cellbuilder/docCodec");

test("groups wins outright: a legacy structures list alongside real groups is ignored", () => {
  const doc = {
    groups: [{ name: "Hull", blueprint: "bp" }],
    structures: [{ NAME: "Deck", X: 5 }],
  } as unknown as ProceduralDoc;
  assert.deepEqual(groupsFromDoc(doc), [{ name: "Hull", blueprint: "bp" }]);
});

test("no groups: a legacy structures entry becomes a group at the same origin", () => {
  const doc = {
    blueprint_name: "steel_stru",
    structures: [{ NAME: "Hull", X: 100, Y: 0, Z: 0 }],
  } as unknown as ProceduralDoc;
  assert.deepEqual(groupsFromDoc(doc), [{ name: "Hull", blueprint: "steel_stru", origin: [100, 0, 0] }]);
});

test("a structure at [0, 0, 0] (or with no X/Y/Z at all) gets no origin field, same as a group never given one", () => {
  const doc = { structures: [{ NAME: "Hull" }] } as unknown as ProceduralDoc;
  assert.deepEqual(groupsFromDoc(doc), [{ name: "Hull", blueprint: "" }]);
});

test("INCLUDE: false on a legacy structure carries over as the group's include: false", () => {
  const doc = {
    structures: [
      { NAME: "Hull", INCLUDE: true },
      { NAME: "Deck", INCLUDE: false },
    ],
  } as unknown as ProceduralDoc;
  assert.deepEqual(groupsFromDoc(doc), [
    { name: "Hull", blueprint: "" },
    { name: "Deck", blueprint: "", include: false },
  ]);
});

test("no groups and no structures: still empty, as an ungrouped doc always was", () => {
  assert.deepEqual(groupsFromDoc({} as unknown as ProceduralDoc), []);
});

// A legacy structure entry can carry its own extra columns too (another tool's per-structure
// settings), same as a `groups[]` entry can -- copied across the same way, as python does when
// it folds `structures` into `groups`, rather than dropped the moment this legacy shape is read.
test("a legacy structure's own extra columns are copied onto the group it becomes", () => {
  const doc = {
    structures: [{ NAME: "Hull", X: 100, MODEL_INPUT: "run-42", CONDITION_NAME: "Design" }],
  } as unknown as ProceduralDoc;
  assert.deepEqual(groupsFromDoc(doc), [
    { name: "Hull", blueprint: "", origin: [100, 0, 0], MODEL_INPUT: "run-42", CONDITION_NAME: "Design" },
  ]);
});

test("through the store: a legacy structures-only doc opens with the structure as a group, and the next commit writes groups, not structures", () => {
  const doc = {
    schema_version: "1.1",
    grid: {},
    blueprint: {},
    spaces: [{ NAME: "Room", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, STRUCTURE_NAME: "Hull" }],
    equipments: [],
    structures: [{ NAME: "Hull", X: 100, Y: 0, Z: 0 }],
  } as unknown as ProceduralDoc;
  useCellBuilderStore.getState().open("m", "Model", 1, doc);
  const st = useCellBuilderStore.getState();
  assert.deepEqual(st.groups, [{ name: "Hull", blueprint: "", origin: [100, 0, 0] }]);
  const out = st.toDoc() as unknown as Record<string, unknown>;
  assert.deepEqual(out.groups, [{ name: "Hull", blueprint: "", origin: [100, 0, 0] }]);
  assert.equal("structures" in out, false, "the legacy shape is not carried forward once read");
});
