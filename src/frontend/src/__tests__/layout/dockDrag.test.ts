import assert from "node:assert/strict";
import { test } from "node:test";

import { DROP_ATTR, dockDragStore, dropZoneAt, endDockDrag, moveDockDrag, pointerOf, type DropCandidate } from "@/layout/dockDrag";

const el = (zone: string | null): DropCandidate => ({
  closest: (sel: string) => {
    assert.equal(sel, `[${DROP_ATTR}]`);
    return zone ? { getAttribute: () => zone } : null;
  },
});

test("the first drop target under the pointer wins", () => {
  assert.equal(dropZoneAt([el(null), el("zone-a"), el("zone-b")]), "zone-a");
  assert.equal(dropZoneAt([el(null), {}]), null);
  assert.equal(dropZoneAt([]), null);
});

test("a drag shows the targets on its first move and tracks the zone under the pointer after", () => {
  const hit = (x: number) => (x < 100 ? "column" : null);
  moveDockDrag("p", 50, 0, hit);
  assert.deepEqual(dockDragStore.getState(), { panel: "p", over: null });
  moveDockDrag("p", 50, 0, hit);
  assert.equal(dockDragStore.getState().over, "column");
  moveDockDrag("p", 500, 0, hit);
  assert.equal(dockDragStore.getState().over, null);
  assert.equal(endDockDrag(50, 0, hit), "column");
  assert.deepEqual(dockDragStore.getState(), { panel: null, over: null });
});

test("a drag that never moved drops nowhere", () => {
  assert.equal(endDockDrag(50, 0, () => "column"), null);
});

test("the pointer of a mouse event, or of a touch's changed touch", () => {
  assert.deepEqual(pointerOf({ clientX: 3, clientY: 4 }), { x: 3, y: 4 });
  assert.deepEqual(pointerOf({ changedTouches: [{ clientX: 5, clientY: 6 }] }), { x: 5, y: 6 });
  assert.equal(pointerOf({}), null);
});
