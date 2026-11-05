import assert from "node:assert/strict";
import { test } from "node:test";

import { arrowDirection, cycleZone, orderZones, zoneKeyIntent, type ZoneKeyContext, type ZoneKeyEvent } from "@/layout/zoneKeys";

const key = (k: string, mods: Partial<ZoneKeyEvent> = {}): ZoneKeyEvent => ({
  key: k,
  shiftKey: false,
  ctrlKey: false,
  altKey: false,
  metaKey: false,
  ...mods,
});
const inFloat: ZoneKeyContext = { inFloat: true, inTextField: false, overlayOpen: false };
const outside: ZoneKeyContext = { inFloat: false, inTextField: false, overlayOpen: false };

test("F6 and Shift+F6 cycle zones from anywhere, a text field included", () => {
  assert.equal(zoneKeyIntent(key("F6"), outside), "next-zone");
  assert.equal(zoneKeyIntent(key("F6", { shiftKey: true }), { ...inFloat, inTextField: true }), "prev-zone");
  assert.equal(zoneKeyIntent(key("F6", { ctrlKey: true }), outside), null);
  assert.equal(zoneKeyIntent(key("F6", { altKey: true }), outside), null);
});

test("Esc closes a focused window, unless a field, a menu or a dialog has it", () => {
  assert.equal(zoneKeyIntent(key("Escape"), inFloat), "close-float");
  assert.equal(zoneKeyIntent(key("Escape"), outside), null);
  assert.equal(zoneKeyIntent(key("Escape"), { ...inFloat, inTextField: true }), null);
  assert.equal(zoneKeyIntent(key("Escape"), { ...inFloat, overlayOpen: true }), null);
  assert.equal(zoneKeyIntent(key("Escape", { defaultPrevented: true }), inFloat), null);
  assert.equal(zoneKeyIntent(key("Escape", { shiftKey: true }), inFloat), null);
});

test("Ctrl+Alt+arrow docks a focused window; other arrow chords do not", () => {
  for (const k of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) {
    assert.equal(zoneKeyIntent(key(k, { ctrlKey: true, altKey: true }), inFloat), "dock-float");
  }
  assert.equal(zoneKeyIntent(key("ArrowLeft", { ctrlKey: true, altKey: true }), outside), null);
  assert.equal(zoneKeyIntent(key("ArrowLeft", { ctrlKey: true }), inFloat), null);
  assert.equal(zoneKeyIntent(key("ArrowLeft", { shiftKey: true }), inFloat), null);
  assert.equal(zoneKeyIntent(key("ArrowLeft", { ctrlKey: true, altKey: true, shiftKey: true }), inFloat), null);
});

test("Ctrl+Alt+arrow moves a focused docked panel where the preset moves panels by direction", () => {
  const inDock: ZoneKeyContext = { ...outside, inDock: true };
  assert.equal(zoneKeyIntent(key("ArrowDown", { ctrlKey: true, altKey: true }), inDock), "move-docked");
  assert.equal(zoneKeyIntent(key("ArrowDown", { ctrlKey: true, altKey: true }), outside), null);
  assert.equal(zoneKeyIntent(key("ArrowDown", { ctrlKey: true, altKey: true }), { ...inDock, inTextField: true }), null);
  assert.equal(zoneKeyIntent(key("ArrowDown", { ctrlKey: true }), inDock), null);
  assert.equal(zoneKeyIntent(key("Escape"), inDock), null, "Esc closes windows only");
  assert.equal(zoneKeyIntent(key("ArrowUp", { ctrlKey: true, altKey: true }), { ...inFloat, inDock: true }), "dock-float");
  assert.equal(arrowDirection("ArrowLeft"), "left");
  assert.equal(arrowDirection("ArrowDown"), "down");
  assert.equal(arrowDirection("Enter"), null);
});

test("cycling wraps, and starts at an end when focus is in no zone", () => {
  assert.equal(cycleZone(-1, 4, 1), 0);
  assert.equal(cycleZone(-1, 4, -1), 3);
  assert.equal(cycleZone(3, 4, 1), 0);
  assert.equal(cycleZone(0, 4, -1), 3);
  assert.equal(cycleZone(1, 4, 1), 2);
  assert.equal(cycleZone(0, 0, 1), -1);
});

test("zones order by kind, windows among themselves by document order", () => {
  const zones = [
    { k: "float", n: "w1" },
    { k: "viewport", n: "v" },
    { k: "column", n: "c" },
    { k: "float", n: "w2" },
    { k: "toolbar", n: "t" },
    { k: "other", n: "x" },
  ];
  assert.deepEqual(
    orderZones(zones, (z) => z.k).map((z) => z.n),
    ["t", "c", "w1", "w2", "v", "x"],
  );
  assert.deepEqual(
    orderZones([{ k: "float" }, { k: "dock" }, { k: "toolbar" }], (z) => z.k).map((z) => z.k),
    ["toolbar", "dock", "float"],
  );
});
