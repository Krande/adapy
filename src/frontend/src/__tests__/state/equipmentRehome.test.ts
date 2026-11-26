// Moved equipment kept its old space: containingCellName (docCodec.ts) trusts a stored SPACE_NAME
// that still resolves to SOME space, and before this fix, nothing updated it after a drag -- only
// a GLOBAL opening's SPACE_NAME/SPACE_SIDE followed a move (cellsSlice.ts's
// rehomeGlobalOpeningParams). cellsSlice.ts's translateCellAlongAxis/updateCell now also call
// utils/cellbuilder/equipmentHost.ts's rehomeEquipmentParams for a moved equipment, scoped to its
// own structure so a structure's origin (its items are stored in its LOCAL frame) cannot confuse
// the lookup with another structure's cell at the same local coordinates.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

// Hull (origin [100, 0, 0]) has spaces A (x: 0-4) and B (x: 10-14). Deck (origin [0, 0, 0], so its
// own items are NOT re-expressed in Hull's frame) has a decoy space at EACH of those same local
// coordinates -- one at A's, one at B's -- and both decoys are inserted FIRST, ahead of A and B
// themselves. A naive, structure-unaware nearest-footprint search iterates candidates in
// insertion order and returns the first one whose footprint matches, so with the decoys first it
// would land equipment moved into B on Deck's decoy instead (and the "off every space" fallback
// on Deck's decoy at A's spot instead of Hull's own A) -- unless the search is actually scoped to
// the equipment's own structure, which is the thing under test.
function model() {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: {
      decoyA: { id: "decoyA", name: "DecoyA", kind: "cell", group: "Deck", origin: [0, 0, 0], size: [4, 4, 3] },
      decoyB: { id: "decoyB", name: "DecoyB", kind: "cell", group: "Deck", origin: [10, 0, 0], size: [4, 4, 3] },
      a: { id: "a", name: "A", kind: "cell", group: "Hull", origin: [0, 0, 0], size: [4, 4, 3] },
      b: { id: "b", name: "B", kind: "cell", group: "Hull", origin: [10, 0, 0], size: [4, 4, 3] },
      eq: {
        id: "eq",
        name: "Eq",
        kind: "equipment",
        origin: [1, 1, 0],
        size: [1, 1, 1],
        params: { STRUCTURE_NAME: "Hull", SPACE_NAME: "A", SPACE_LOC: "FLOOR" },
      },
    },
    groups: [
      { name: "Hull", blueprint: "bp", origin: [100, 0, 0] },
      { name: "Deck", blueprint: "bp" },
    ],
    loftMembers: [],
    activeGroup: null,
    selection: null,
    selectedCellIds: [],
  } as never);
}
const st = () => useCellBuilderStore.getState();

test("updateCell moving equipment from A into B re-homes SPACE_NAME to B, not Deck's decoy at the same local coordinates", () => {
  model();
  st().updateCell("eq", { origin: [11, 1, 0] });
  assert.equal(st().cells.eq.params?.SPACE_NAME, "B");
  const doc = st().toDoc();
  assert.equal((doc.equipments as { SPACE_NAME?: string }[])[0]?.SPACE_NAME, "B");
});

test("translateCellAlongAxis moving equipment from A into B re-homes SPACE_NAME the same way", () => {
  model();
  // A -> B is +10 along X.
  st().translateCellAlongAxis("eq", 0, 10);
  assert.equal(st().cells.eq.params?.SPACE_NAME, "B");
});

test("a move within A's own footprint keeps SPACE_NAME as A", () => {
  model();
  st().updateCell("eq", { origin: [2, 2, 0] });
  assert.equal(st().cells.eq.params?.SPACE_NAME, "A");
});

test("a move off every space in its structure drops SPACE_NAME", () => {
  model();
  st().updateCell("eq", { origin: [40, 40, 0] });
  assert.equal(st().cells.eq.params?.SPACE_NAME, undefined);
  // toDoc's fallback keeps it inside its own structure (A, the first space of Hull) rather than
  // falling through to Deck's decoy.
  const doc = st().toDoc();
  assert.equal((doc.equipments as { SPACE_NAME?: string }[])[0]?.SPACE_NAME, "A");
});

test("the current SPACE_NAME wins over a geometrically nearer host in the same structure", () => {
  model();
  // A second space stacked directly above A: its floor (Z = 0.9) ends up CLOSER to eq's new Z
  // (0.8) than A's own floor (Z = 0) is, so a plain nearest-surface pick would jump to it -- but
  // eq's CURRENT seat (A) is still among the hosts (both share its X/Y footprint), so it must stay.
  useCellBuilderStore.setState({
    cells: {
      ...st().cells,
      aUpper: { id: "aUpper", name: "AUpper", kind: "cell", group: "Hull", origin: [0, 0, 0.9], size: [4, 4, 3] },
    },
  } as never);
  st().updateCell("eq", { origin: [1.5, 1.5, 0.8] });
  assert.equal(st().cells.eq.params?.SPACE_NAME, "A");
});
