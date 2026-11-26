// `snapshotFrom` (components/info_box_selected_object/selectionSnapshot.ts): the pure decision
// behind useSelectionSnapshot, run with no React, no store subscriptions -- just the bag of raw
// values the hook gathers.

import assert from "node:assert/strict";
import { test } from "node:test";

// Enough of a browser for the modules snapshotFrom pulls in (isMirroredWindow etc.) to load.
await import("../fixtures/panelRealm");

const { snapshotFrom } = await import("@/components/info_box_selected_object/selectionSnapshot");

type Inputs = Parameters<typeof snapshotFrom>[0];

const base: Inputs = {
  meshPickName: "",
  treeLoaded: false,
  cbActive: false,
  cbSelection: null,
  cbSelectedIds: [],
  cbSelectedRefIds: [],
  cbCells: {},
  cbRefs: {},
  cbGroups: [],
  cbLoftMembers: [],
  cbActiveGroup: null,
  rangeCount: 0,
  builderShown: true,
};

const room = { id: "c1", name: "Room", kind: "cell" as const, group: "Hull", origin: [0, 0, 0] as [number, number, number], size: [4, 4, 3] as [number, number, number], params: {} };

test("nothing picked, nothing loaded: kind 'none', no structure", () => {
  const snap = snapshotFrom(base);
  assert.equal(snap.kind, "none");
  assert.equal(snap.name, null);
  assert.equal(snap.structure, undefined);
  assert.equal(snap.hasEntities, false);
});

test("the builder pick wins over a mesh pick when both are present ('any')", () => {
  const snap = snapshotFrom({
    ...base,
    cbActive: true,
    cbSelection: { kind: "cell", cellId: "c1" },
    cbCells: { c1: room },
    meshPickName: "StaleMeshPick",
    rangeCount: 1,
  });
  assert.equal(snap.kind, "cell");
  assert.equal(snap.name, "Room");
});

test("selectionSource='builder' never falls through to a mesh pick, even with nothing of the builder's own selected", () => {
  const snap = snapshotFrom({ ...base, meshPickName: "StaleMeshPick", rangeCount: 1 }, "builder");
  assert.equal(snap.kind, "none");
  assert.equal(snap.name, null);
});

test("selectionSource='scene' never shows a cell pick, even with one made", () => {
  const snap = snapshotFrom(
    {
      ...base,
      cbActive: true,
      cbSelection: { kind: "cell", cellId: "c1" },
      cbCells: { c1: room },
    },
    "scene",
  );
  assert.equal(snap.kind, "none");
});

test("selectionSource='scene' still shows a mesh pick", () => {
  const snap = snapshotFrom({ ...base, meshPickName: "E1", rangeCount: 1 }, "scene");
  assert.equal(snap.kind, "mesh");
  assert.equal(snap.name, "E1");
});

test("nothing picked, a procedural model open with an active structure: 'none' carries it", () => {
  const snap = snapshotFrom({
    ...base,
    cbActive: true,
    cbGroups: [{ name: "Hull", blueprint: "bp" }],
    cbActiveGroup: "Hull",
  });
  assert.equal(snap.kind, "none");
  assert.equal(snap.structure, "Hull");
});

test("an active structure that no longer exists in groups is dropped, not carried as stale", () => {
  const snap = snapshotFrom({
    ...base,
    cbActive: true,
    cbGroups: [{ name: "Deck", blueprint: "bp" }],
    cbActiveGroup: "Hull",
  });
  assert.equal(snap.structure, undefined);
});

test("builderShown=false: the builder's own selection does not show, even active with a selection", () => {
  const snap = snapshotFrom({
    ...base,
    cbActive: true,
    cbSelection: { kind: "cell", cellId: "c1" },
    cbCells: { c1: room },
    builderShown: false,
  });
  assert.notEqual(snap.kind, "cell");
});

test("a ref selection reports kind 'ref' with its owner/key", () => {
  const ref = { id: "r1", name: "Load 1", owner: "a-plugin", key: "load:1" } as never;
  const snap = snapshotFrom({
    ...base,
    cbActive: true,
    cbSelection: { kind: "ref", refId: "r1" },
    cbRefs: { r1: ref },
  });
  assert.equal(snap.kind, "ref");
  assert.equal(snap.refOwner, "a-plugin");
  assert.equal(snap.refKey, "load:1");
});

test("shellMode is carried onto the snapshot only when given", () => {
  assert.equal(snapshotFrom(base).shellMode, undefined);
  assert.equal(snapshotFrom({ ...base, shellMode: "output" }).shellMode, "output");
});
