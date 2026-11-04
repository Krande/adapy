import assert from "node:assert/strict";
import { test } from "node:test";

import type { ZoneDef } from "@/layout/layout";
import {
  LAYOUT_DOC_VERSION,
  adoptNewPanels,
  closePanel,
  emptyLayoutDoc,
  isOpenIn,
  migrateLayoutDoc,
  movePanel,
  openPanel,
  raiseFloat,
  setZoneCollapsed,
  setZoneSize,
  whereOpen,
} from "@/layout/reducers";

const ZONES: ZoneDef[] = [
  { id: "left", kind: "dock", label: "Left", edge: "left", limits: { min: 200, max: 600, default: 320 } },
  { id: "right", kind: "dock", label: "Right", edge: "right", arrangement: "tabs" },
  { id: "float", kind: "float", label: "Windows" },
];
const ORDER: Record<string, number> = { a: 10, b: 20, c: 30 };
const order = (id: string) => ORDER[id];

test("an empty document has every zone, sized to its default, and nothing open", () => {
  const d = emptyLayoutDoc("p", ZONES);
  assert.equal(d.v, LAYOUT_DOC_VERSION);
  assert.deepEqual(Object.keys(d.zones), ["left", "right", "float"]);
  assert.equal(d.zones.left.size, 320);
  assert.equal(d.zones.right.size, undefined);
  assert.deepEqual(d.known, []);
  assert.equal(whereOpen(d, "a"), null);
});

test("opening places a panel in zone order and makes it active", () => {
  let d = emptyLayoutDoc("p", ZONES);
  d = openPanel(d, "c", { zone: "right", order });
  d = openPanel(d, "a", { zone: "right", order });
  d = openPanel(d, "b", { zone: "right", order });
  assert.deepEqual(d.zones.right.panels, ["a", "b", "c"]);
  assert.equal(d.zones.right.active, "b");
});

test("opening an open panel only activates it; opening it elsewhere moves it", () => {
  let d = openPanel(emptyLayoutDoc("p", ZONES), "a", { zone: "right" });
  d = openPanel(d, "b", { zone: "right" });
  const again = openPanel(d, "a", { zone: "right" });
  assert.deepEqual(again.zones.right.panels, ["a", "b"]);
  assert.equal(again.zones.right.active, "a");
  const moved = openPanel(d, "a", { zone: "left" });
  assert.deepEqual(moved.zones.right.panels, ["b"]);
  assert.deepEqual(moved.zones.left.panels, ["a"]);
});

test("closing activates the next panel of the zone, and is a no-op when closed", () => {
  let d = emptyLayoutDoc("p", ZONES);
  for (const id of ["a", "b", "c"]) d = openPanel(d, id, { zone: "right", order });
  d = openPanel(d, "b", { zone: "right", order });
  d = closePanel(d, "b");
  assert.deepEqual(d.zones.right.panels, ["a", "c"]);
  assert.equal(d.zones.right.active, "c");
  d = closePanel(d, "c");
  assert.equal(d.zones.right.active, "a");
  assert.equal(closePanel(d, "zzz"), d, "closing a closed panel returns the same document");
});

test("an exclusive group: opening one closes the others", () => {
  let d = openPanel(emptyLayoutDoc("p", ZONES), "admin", { zone: "float", float: true });
  d = openPanel(d, "convert", { zone: "float", float: true }, ["admin", "convert"]);
  assert.equal(isOpenIn(d, "admin"), false);
  assert.equal(isOpenIn(d, "convert"), true);
});

test("floating keeps the panel's rect and stacks the newest on top", () => {
  let d = openPanel(emptyLayoutDoc("p", ZONES), "a", { zone: "float", float: true, rect: { x: 10, y: 20, w: 300, h: 200 } });
  d = openPanel(d, "b", { zone: "float", float: true });
  assert.deepEqual(d.floats.a, { x: 10, y: 20, w: 300, h: 200, z: 1 });
  assert.equal(d.floats.b.z, 2);
  d = raiseFloat(d, "a");
  assert.equal(d.floats.a.z, 3);
  assert.equal(raiseFloat(d, "a"), d, "already on top");
  // Reopened, it comes back where it was.
  d = openPanel(d, "a", { zone: "float", float: true, rect: { x: 0, y: 0, w: 1, h: 1 } });
  assert.equal(d.floats.a.x, 10);
});

test("floating a docked panel undocks it, and docking a float unfloats it", () => {
  let d = openPanel(emptyLayoutDoc("p", ZONES), "a", { zone: "right" });
  d = openPanel(d, "a", { zone: "float", float: true });
  assert.deepEqual(d.zones.right.panels, []);
  assert.equal(whereOpen(d, "a"), "float");
  d = openPanel(d, "a", { zone: "left" });
  assert.equal(d.floats.a, undefined);
  assert.equal(whereOpen(d, "a"), "left");
});

test("opening a minimised panel restores it", () => {
  const d = { ...openPanel(emptyLayoutDoc("p", ZONES), "a", { zone: "right" }), minimised: ["a"] };
  assert.deepEqual(openPanel(d, "a", { zone: "right" }).minimised, []);
  assert.deepEqual(closePanel(d, "a").minimised, []);
});

test("movePanel inserts at an index, clamped", () => {
  let d = emptyLayoutDoc("p", ZONES);
  for (const id of ["a", "b"]) d = openPanel(d, id, { zone: "right" });
  d = movePanel(d, "c", "right", 1);
  assert.deepEqual(d.zones.right.panels, ["a", "c", "b"]);
  d = movePanel(d, "a", "right", 99);
  assert.deepEqual(d.zones.right.panels, ["c", "b", "a"]);
});

test("zone size is clamped to the zone's limits; collapse is recorded", () => {
  const d = emptyLayoutDoc("p", ZONES);
  assert.equal(setZoneSize(d, "left", 50, ZONES[0]).zones.left.size, 200);
  assert.equal(setZoneSize(d, "left", 450, ZONES[0]).zones.left.size, 450);
  assert.equal(setZoneSize(d, "nope", 1), d);
  assert.equal(setZoneCollapsed(d, "left", true).zones.left.collapsed, true);
});

test("new panels are adopted once", () => {
  const d = emptyLayoutDoc("p", ZONES);
  const first = adoptNewPanels(d, ["a", "b"]);
  assert.deepEqual(first.adopted, ["a", "b"]);
  const second = adoptNewPanels(first.doc, ["a", "b", "c"]);
  assert.deepEqual(second.adopted, ["c"]);
  assert.deepEqual(second.doc.known, ["a", "b", "c"]);
  const third = adoptNewPanels(second.doc, ["c"]);
  assert.equal(third.doc, second.doc);
});

test("migration: a foreign, older or malformed document starts over", () => {
  const fresh = emptyLayoutDoc("p", ZONES);
  assert.deepEqual(migrateLayoutDoc(null, "p", ZONES), fresh);
  assert.deepEqual(migrateLayoutDoc({ v: 0, preset: "p" }, "p", ZONES), fresh);
  assert.deepEqual(migrateLayoutDoc({ v: LAYOUT_DOC_VERSION, preset: "other" }, "p", ZONES), fresh);
});

test("migration: keeps what is valid, drops what is not, follows the preset's zones", () => {
  const raw = {
    v: LAYOUT_DOC_VERSION,
    preset: "p",
    zones: {
      right: { panels: ["a", "a", 3, "plugin:x"], active: "gone", size: 250 },
      retired: { panels: ["b"], active: "b" },
    },
    floats: { c: { x: 1, y: 2, w: 3, h: 4, z: 5 }, bad: { x: 1, y: 2, w: -1, h: 4 } },
    minimised: ["c", "c"],
    known: ["a", "b", "c", 7],
  };
  const d = migrateLayoutDoc(raw, "p", ZONES);
  assert.deepEqual(Object.keys(d.zones), ["left", "right", "float"]);
  // An id nothing has registered yet is kept: a plugin may register it later.
  assert.deepEqual(d.zones.right.panels, ["a", "plugin:x"]);
  assert.equal(d.zones.right.active, "a");
  assert.equal(d.zones.right.size, 250);
  assert.equal(d.zones.left.size, 320);
  assert.deepEqual(Object.keys(d.floats), ["c"]);
  assert.deepEqual(d.minimised, ["c"]);
  assert.deepEqual(d.known, ["a", "b", "c"]);
});

test("reducers never mutate their input", () => {
  const d = emptyLayoutDoc("p", ZONES);
  const snapshot = JSON.stringify(d);
  openPanel(d, "a", { zone: "right" });
  openPanel(d, "b", { zone: "float", float: true });
  movePanel(d, "a", "left", 0);
  setZoneSize(d, "left", 400, ZONES[0]);
  adoptNewPanels(d, ["x"]);
  assert.equal(JSON.stringify(d), snapshot);
});
