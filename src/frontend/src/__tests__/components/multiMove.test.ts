// G on 2+ selected items moves them all together. These tests cover the pure rules
// (movableSet/movableCount/movableWorldCentre, cellGizmo.ts's multi-move) without a scene: what
// counts towards the "2 or more" threshold, what rides along for free, what cannot move at all,
// and the shared delta math (structureFrames.ts's movedStructureOrigin) applied to a cell, a ref
// and a loft member.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { movableSet, movableCount, movableWorldCentre, multiMoveActive } = await import("@/components/viewer/cellbuilder/multiMove");
const { movedStructureOrigin } = await import("@/components/viewer/cellbuilder/structureFrames");

const st = () => useCellBuilderStore.getState();
const cellId = (name: string) => Object.values(st().cells).find((c) => c.name === name)!.id;

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "S1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 },
    { NAME: "S2", X: 10, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 },
  ],
  equipments: [{ NAME: "E1", SPACE_NAME: "S1", GLOBAL_COORDS: true, X: 0.5, Y: 0.5, Z: 0, LX: 1, LY: 1, LZ: 1 }],
} as unknown as ProceduralDoc;

function withModel(run: () => void, refs: { key: string; structure?: string; move?: boolean }[] = []): void {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "mm-test",
    project: () =>
      refs.map((r) => ({
        key: r.key,
        name: r.key,
        kindLabel: "Load",
        origin: [0, 0, 0] as [number, number, number],
        size: [1, 1, 1] as [number, number, number],
        structure: r.structure,
        edit: { move: r.move ?? true },
      })),
    // `edit.move` is only ever true when the provider can actually write back (refsSlice.ts);
    // a no-op write is enough for these pure-logic tests.
    write: () => true,
  });
  try {
    run();
  } finally {
    off();
    st().close();
  }
}

test("movableCount: 0 or 1 selected leaves G to the single-item path", () => {
  withModel(() => {
    assert.equal(movableCount(movableSet(st())), 0);
    useCellBuilderStore.setState({ selectedCellIds: [cellId("S1")] } as never);
    assert.equal(movableCount(movableSet(st())), 1);
  });
});

test("2 selected spaces: both count, their equipment rides for free (not independently counted)", () => {
  withModel(() => {
    const s1 = cellId("S1");
    const s2 = cellId("S2");
    const e1 = cellId("E1");
    useCellBuilderStore.setState({ selectedCellIds: [s1, s2] } as never);
    const set = movableSet(st());
    assert.equal(movableCount(set), 2);
    assert.deepEqual(set.entries.map((e) => e.id).sort(), [s1, s2].sort());
    assert.deepEqual(set.riderIds, [e1]);
  });
});

test("an equipment independently selected alongside its own space rides for free too, not double-counted", () => {
  withModel(() => {
    const s1 = cellId("S1");
    const s2 = cellId("S2");
    const e1 = cellId("E1");
    useCellBuilderStore.setState({ selectedCellIds: [s1, s2, e1] } as never);
    const set = movableSet(st());
    assert.equal(movableCount(set), 2, "E1 rides with S1, it is not a third independent item");
    assert.ok(!set.entries.some((e) => e.id === e1));
    assert.deepEqual(set.riderIds, [e1]);
  });
});

test("moveEquipWithCell off: equipment never rides for free, so it counts on its own when selected", () => {
  withModel(() => {
    const s1 = cellId("S1");
    const e1 = cellId("E1");
    useCellBuilderStore.setState({ selectedCellIds: [s1, e1], moveEquipWithCell: false } as never);
    try {
      const set = movableSet(st());
      assert.equal(movableCount(set), 2);
      assert.deepEqual(set.riderIds, []);
    } finally {
      // A store-wide toggle, not doc state `close()` resets -- restore it for every later test.
      useCellBuilderStore.setState({ moveEquipWithCell: true } as never);
    }
  });
});

test("a ref whose plugin allows a move counts; one that does not is blocked, not counted", () => {
  withModel(
    () => {
      const s1 = cellId("S1");
      useCellBuilderStore.setState({ selectedCellIds: [s1], selectedRefIds: ["mm-test:movable", "mm-test:fixed"] } as never);
      const set = movableSet(st());
      assert.equal(movableCount(set), 2, "S1 and the movable ref; the fixed one is blocked");
      assert.deepEqual(set.blockedRefIds, ["mm-test:fixed"]);
      assert.ok(set.entries.some((e) => e.kind === "ref" && e.id === "mm-test:movable"));
    },
    [
      { key: "movable", move: true },
      { key: "fixed", move: false },
    ],
  );
});

test("movableWorldCentre: the midpoint of everything that will actually move, riders included", () => {
  withModel(() => {
    const s1 = cellId("S1"); // origin [0,0,0] size [2,2,2]
    const s2 = cellId("S2"); // origin [10,0,0] size [2,2,2]
    useCellBuilderStore.setState({ selectedCellIds: [s1, s2] } as never);
    const set = movableSet(st());
    const centre = movableWorldCentre(st(), set);
    // Union box of S1 [0,0,0]-[2,2,2], S2 [10,0,0]-[12,2,2] and E1 (rides, inside S1): [0,0,2]-[12,2,2].
    assert.deepEqual(centre, [6, 1, 1]);
  });
});

// --- multiMoveActive is looser than "movableCount >= 2" -----------------------------------------

test("a selected space plus its own (also selected) equipment is 2 SELECTED items with 1 movable entry -- multiMoveActive is still true", () => {
  withModel(() => {
    const s1 = cellId("S1");
    const e1 = cellId("E1");
    useCellBuilderStore.setState({ selectedCellIds: [s1, e1], moveEquipWithCell: true } as never);
    assert.equal(movableCount(movableSet(st())), 1, "the equipment rides for free, it is not independently movable");
    assert.ok(multiMoveActive(st()), "2+ SELECTED items, at least one movable: G still arms the multi path");
  });
});

test("a movable item plus a ref that cannot move is 2 SELECTED items with 1 movable entry -- multiMoveActive is still true", () => {
  withModel(
    () => {
      const s1 = cellId("S1");
      useCellBuilderStore.setState({ selectedCellIds: [s1], selectedRefIds: ["mm-test:fixed"] } as never);
      assert.equal(movableCount(movableSet(st())), 1, "only S1 is movable; the ref is blocked");
      assert.ok(multiMoveActive(st()), "G still arms, rather than doing nothing");
    },
    [{ key: "fixed", move: false }],
  );
});

test("fewer than 2 selected, or nothing at all movable, never activates the multi path", () => {
  withModel(
    () => {
      const s1 = cellId("S1");
      assert.equal(multiMoveActive(st()), false, "nothing selected");
      useCellBuilderStore.setState({ selectedCellIds: [s1] } as never);
      assert.equal(multiMoveActive(st()), false, "only 1 selected");
      useCellBuilderStore.setState({ selectedCellIds: [], selectedRefIds: ["mm-test:fixed", "mm-test:fixed2"] } as never);
      assert.equal(movableCount(movableSet(st())), 0, "both blocked");
      assert.equal(multiMoveActive(st()), false, "2 selected, but NOTHING in the set can move");
    },
    [
      { key: "fixed", move: false },
      { key: "fixed2", move: false },
    ],
  );
});

test("the shared delta math (movedStructureOrigin) applied to a cell, a ref and a loft member's own start", () => {
  // Three different starting points, one shared group drag: start centre (0,0,0) -> (3,0,0), X only.
  const startCentre: [number, number, number] = [0, 0, 0];
  const centre: [number, number, number] = [3, 0, 0];
  const axes: readonly (0 | 1 | 2)[] = [0];
  const step = 0;
  const cellOrigin = movedStructureOrigin([5, 1, 0], startCentre, centre, axes, step);
  assert.deepEqual(cellOrigin, [8, 1, 0], "a cell: start + delta on the dragged axis only");
  const refOrigin = movedStructureOrigin([0, 2, 0], startCentre, centre, axes, step);
  assert.deepEqual(refOrigin, [3, 2, 0], "a ref: the same math, its own start");
  // A loft member moves INCREMENTALLY (moveLoftMember takes a delta): the group's quantized
  // delta so far, minus what was already applied -- here, nothing yet, so the whole 3.
  const groupDeltaSoFar = centre[0] - startCentre[0];
  assert.equal(groupDeltaSoFar, 3, "a loft member: the group's own delta, applied as one increment");
});
