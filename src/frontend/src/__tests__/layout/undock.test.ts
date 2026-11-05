import assert from "node:assert/strict";
import { test } from "node:test";

import type { LayoutPrefs, ZoneDef } from "@/layout/layout";
import { LAYOUT_PREFS_STORAGE_KEY, localLayoutPersistence, type PrefsStorage } from "@/layout/localPersistence";
import { createLayoutStore } from "@/layout/layoutStore";
import {
  dockPanel,
  emptyLayoutDoc,
  floatPanel,
  migrateLayoutDoc,
  minimisePanel,
  restorePanel,
  setFloatRect,
} from "@/layout/reducers";

// Undocking: where a panel shows (column, window, tray), kept apart from whether
// it is open, and persisted with the rest of the user's layout.

const ZONES: ZoneDef[] = [{ id: "col", kind: "stack", label: "Column" }];
const R = { x: 10, y: 20, w: 300, h: 200 };

test("floating puts a panel on top at its rect, and remembers the rect across a dock", () => {
  let d = emptyLayoutDoc("p", ZONES);
  d = floatPanel(d, "a", R);
  d = floatPanel(d, "b", { ...R, x: 50 });
  assert.deepEqual(d.floats.a, { ...R, z: 1 });
  assert.equal(d.floats.b.z, 2);
  d = setFloatRect(d, "a", { x: 1, y: 2, w: 400, h: 300 });
  assert.deepEqual(d.floats.a, { x: 1, y: 2, w: 400, h: 300, z: 1 });
  assert.equal(setFloatRect(d, "a", { x: 1, y: 2, w: 400, h: 300 }), d, "an unchanged rect is no change");
  assert.equal(setFloatRect(d, "c", R), d, "a panel that is not floated has no rect to set");

  const refloat = floatPanel(d, "a", { x: 999, y: 999, w: 1, h: 1 });
  assert.equal(refloat.floats.a.x, 1, "floating again keeps where it was");
  assert.equal(refloat.floats.a.z, 3, "and brings it to the front");

  d = dockPanel(d, "a");
  assert.equal(d.floats.a, undefined);
  assert.equal(dockPanel(d, "a"), d, "docking a docked panel is no change");
  assert.deepEqual(d.zones.col.panels, [], "docking never opens anything");
});

test("minimising and restoring move a panel in and out of the tray", () => {
  let d = floatPanel(floatPanel(emptyLayoutDoc("p", ZONES), "a", R), "b", R);
  d = minimisePanel(d, "a");
  assert.deepEqual(d.minimised, ["a"]);
  assert.equal(minimisePanel(d, "a"), d);
  d = restorePanel(d, "a");
  assert.deepEqual(d.minimised, []);
  assert.equal(d.floats.a.z, 3, "a restored window comes to the front");
  assert.equal(restorePanel(d, "a"), d);

  d = dockPanel(minimisePanel(d, "b"), "b");
  assert.deepEqual(d.minimised, [], "docking takes a panel out of the tray");
  d = floatPanel(minimisePanel(d, "a"), "a", R);
  assert.deepEqual(d.minimised, [], "floating does too");
});

test("floats and the tray survive a save and a load", () => {
  let d = minimisePanel(floatPanel(emptyLayoutDoc("p", ZONES), "a", R), "a");
  const back = migrateLayoutDoc(JSON.parse(JSON.stringify(d)), "p", ZONES);
  assert.deepEqual(back.floats, d.floats);
  assert.deepEqual(back.minimised, ["a"]);
});

function memoryStorage(): PrefsStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}

test("the local backend keeps the prefs under one key, and reads nothing it cannot parse", async () => {
  const storage = memoryStorage();
  const backend = localLayoutPersistence(storage);
  assert.equal(await backend.load(), null);
  const prefs: LayoutPrefs = { v: 1, customisablePanels: true, docs: {} };
  await backend.save(prefs);
  assert.deepEqual(JSON.parse(storage.data.get(LAYOUT_PREFS_STORAGE_KEY)!), prefs);
  assert.deepEqual(await backend.load(), prefs);

  storage.data.set(LAYOUT_PREFS_STORAGE_KEY, "{not json");
  assert.equal(await backend.load(), null);
});

test("blocked storage keeps the prefs for the page", async () => {
  const blocked: PrefsStorage = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };
  const backend = localLayoutPersistence(blocked);
  assert.equal(await backend.load(), null);
  await backend.save({ v: 1, docs: {} });
  assert.equal(await localLayoutPersistence(null).load(), null);
});

test("the store saves the setting and the floats through the local backend", async () => {
  const storage = memoryStorage();
  const store = createLayoutStore();
  await store.getState().hydrate(localLayoutPersistence(storage));
  store.getState().setCustomisablePanels(true);
  store.getState().update("p", ZONES, (d) => floatPanel(d, "a", R));
  await Promise.resolve();

  const again = createLayoutStore();
  await again.getState().hydrate(localLayoutPersistence(storage));
  assert.equal(again.getState().prefs.customisablePanels, true);
  assert.deepEqual(again.getState().doc("p", ZONES).floats.a, { ...R, z: 1 });
});
