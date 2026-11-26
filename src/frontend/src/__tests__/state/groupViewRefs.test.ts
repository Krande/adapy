// Core's own structure eye (the Builder panel's Groups section, groupView.ts) used to hide a
// structure's CELLS only -- a plugin's refs (loads, supports, modifications) filed under it
// stayed drawn. objectIdsOf now gathers both; setGroupHidden/isolateGroup/showAllGroups all go
// through it (or, for showAllGroups, unhideAllCells, which already clears both id lists).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { setGroupHidden, isolateGroup, showAllGroups } = await import("@/utils/cellbuilder/groupView");
const { useGroupViewStore } = await import("@/state/cellbuilder/groupViewStore");

const st = () => useCellBuilderStore.getState();

const box = { origin: [0, 0, 0] as [number, number, number], size: [1, 1, 1] as [number, number, number] };

const doc = { schema_version: "1.1", grid: {}, blueprint: {}, spaces: [], equipments: [] } as unknown as ProceduralDoc;

/** A model with one space (Room, in Hull) and two structures (Hull, Topside); a provider
 *  projecting one ref per structure plus one "*" (every structure) ref. */
function withModel(run: () => void): void {
  st().open("m", "Model", 1, doc);
  useCellBuilderStore.setState({
    cells: { s1: { id: "s1", name: "Room", kind: "cell", group: "Hull", ...box } },
    groups: [
      { name: "Hull", blueprint: "bp" },
      { name: "Topside", blueprint: "bp" },
    ],
  } as never);
  useGroupViewStore.getState().setHiddenGroups([]);
  const off = registerBuilderRefs({
    owner: "loads",
    project: () => [
      { key: "0", name: "Load A", kindLabel: "Load", structure: "Hull", ...box },
      { key: "1", name: "Load B", kindLabel: "Load", structure: "Topside", ...box },
      { key: "2", name: "Load *", kindLabel: "Load", structure: "*", ...box },
    ],
  });
  try {
    run();
  } finally {
    off();
    st().close();
  }
}

test("a structure's eye hides its own refs too, not the cells only", () => {
  withModel(() => {
    setGroupHidden("Hull", true);
    assert.deepEqual(st().hiddenCellIds, ["s1"]);
    assert.deepEqual(st().hiddenRefIds, ["loads:0"]);
    setGroupHidden("Hull", false);
    assert.deepEqual(st().hiddenCellIds, []);
    assert.deepEqual(st().hiddenRefIds, []);
  });
});

test('a ref named "*" (every structure) is never hidden by one structure\'s eye', () => {
  withModel(() => {
    setGroupHidden("Hull", true);
    assert.ok(!st().hiddenRefIds.includes("loads:2"));
    setGroupHidden("Topside", true);
    assert.ok(!st().hiddenRefIds.includes("loads:2"));
  });
});

test("isolateGroup keeps only the named structure's cells and refs; everything else (including another structure's refs) hides", () => {
  withModel(() => {
    isolateGroup("Hull");
    assert.deepEqual(st().hiddenCellIds, []); // s1 is Hull's only cell, kept
    assert.deepEqual(st().hiddenRefIds, ["loads:1"]); // Topside's load hides; "*" stays shown
  });
});

test("showAllGroups clears every hidden cell and ref", () => {
  withModel(() => {
    setGroupHidden("Hull", true);
    assert.ok(st().hiddenRefIds.length > 0);
    showAllGroups();
    assert.deepEqual(st().hiddenCellIds, []);
    assert.deepEqual(st().hiddenRefIds, []);
  });
});
