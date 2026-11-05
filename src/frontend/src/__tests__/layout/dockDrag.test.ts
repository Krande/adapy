import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DROP_ATTR,
  DROP_AXIS_ATTR,
  DROP_INDEX_ATTR,
  dockDragStore,
  dropTargetAt,
  dropZoneAt,
  endDockDrag,
  endDockDragAt,
  moveDockDrag,
  pointerOf,
  type DropCandidate,
} from "@/layout/dockDrag";

const el = (zone: string | null): DropCandidate => ({
  closest: (sel: string) => {
    assert.equal(sel, `[${DROP_ATTR}]`);
    return zone ? { getAttribute: (n: string) => (n === DROP_ATTR ? zone : null) } : null;
  },
});

/** A target at `index`, 100 px wide and high from the origin. */
const indexed = (zone: string, index: number, axis?: "x" | "y"): DropCandidate => ({
  closest: () => ({
    getAttribute: (n: string) => (n === DROP_ATTR ? zone : n === DROP_INDEX_ATTR ? String(index) : n === DROP_AXIS_ATTR ? (axis ?? null) : null),
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
  }),
});

test("the first drop target under the pointer wins", () => {
  assert.equal(dropZoneAt([el(null), el("zone-a"), el("zone-b")]), "zone-a");
  assert.equal(dropZoneAt([el(null), {}]), null);
  assert.equal(dropZoneAt([]), null);
});

test("an indexed target inserts before itself, or after past its middle", () => {
  assert.deepEqual(dropTargetAt([el("z")]), { zone: "z", index: null });
  assert.deepEqual(dropTargetAt([indexed("z", 2, "x")], 30, 0), { zone: "z", index: 2 });
  assert.deepEqual(dropTargetAt([indexed("z", 2, "x")], 70, 0), { zone: "z", index: 3 });
  assert.deepEqual(dropTargetAt([indexed("z", 4)], 70, 0), { zone: "z", index: 4 }, "no axis: the index as is");
  assert.deepEqual(dropTargetAt([indexed("z", 0, "y")], 90, 20), { zone: "z", index: 0 });
  assert.deepEqual(dropTargetAt([indexed("z", 0, "y")], 10, 80), { zone: "z", index: 1 });
});

test("a drag shows the targets on its first move and tracks the zone under the pointer after", () => {
  const hit = (x: number) => (x < 100 ? "column" : null);
  moveDockDrag("p", 50, 0, hit);
  assert.deepEqual(dockDragStore.getState(), { panel: "p", over: null, index: null });
  moveDockDrag("p", 50, 0, hit);
  assert.equal(dockDragStore.getState().over, "column");
  moveDockDrag("p", 500, 0, hit);
  assert.equal(dockDragStore.getState().over, null);
  assert.equal(endDockDrag(50, 0, hit), "column");
  assert.deepEqual(dockDragStore.getState(), { panel: null, over: null, index: null });
});

test("a drag tracks the insertion point and drops there", () => {
  const hit = (x: number) => ({ zone: "z", index: x < 50 ? 0 : 1 });
  moveDockDrag("p", 10, 0, hit);
  moveDockDrag("p", 10, 0, hit);
  assert.deepEqual(dockDragStore.getState(), { panel: "p", over: "z", index: 0 });
  moveDockDrag("p", 60, 0, hit);
  assert.equal(dockDragStore.getState().index, 1);
  assert.deepEqual(endDockDragAt(60, 0, hit), { zone: "z", index: 1 });
});

test("a drag that never moved drops nowhere", () => {
  assert.equal(endDockDrag(50, 0, () => "column"), null);
});

test("the pointer of a mouse event, or of a touch's changed touch", () => {
  assert.deepEqual(pointerOf({ clientX: 3, clientY: 4 }), { x: 3, y: 4 });
  assert.deepEqual(pointerOf({ changedTouches: [{ clientX: 5, clientY: 6 }] }), { x: 5, y: 6 });
  assert.equal(pointerOf({}), null);
});
