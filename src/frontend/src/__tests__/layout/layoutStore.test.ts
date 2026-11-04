import assert from "node:assert/strict";
import { test } from "node:test";

import type { ZoneDef } from "@/layout/layout";
import {
  LAYOUT_PREFS_VERSION,
  createLayoutStore,
  memoryLayoutPersistence,
  migrateLayoutPrefs,
} from "@/layout/layoutStore";
import { openPanel } from "@/layout/reducers";

const ZONES: ZoneDef[] = [{ id: "right", kind: "dock", label: "Right" }];

test("a fresh store holds empty prefs and an empty document per preset", () => {
  const s = createLayoutStore();
  assert.deepEqual(s.getState().prefs, { v: LAYOUT_PREFS_VERSION, docs: {} });
  assert.equal(s.getState().hydrated, false);
  assert.deepEqual(s.getState().doc("p", ZONES).zones.right.panels, []);
});

test("update writes the document under its preset (and context) key", () => {
  const s = createLayoutStore();
  s.getState().update("p", ZONES, (d) => openPanel(d, "a", { zone: "right" }));
  s.getState().update("p", ZONES, (d) => openPanel(d, "b", { zone: "right" }), "mode-2");
  assert.deepEqual(Object.keys(s.getState().prefs.docs), ["p", "p:mode-2"]);
  assert.deepEqual(s.getState().doc("p", ZONES).zones.right.panels, ["a"]);
  assert.deepEqual(s.getState().doc("p", ZONES, "mode-2").zones.right.panels, ["b"]);
});

test("nothing is saved before hydrate; after it, every change is", async () => {
  const s = createLayoutStore();
  const backend = memoryLayoutPersistence();
  const saved = () => backend.saved;
  s.getState().setCustomisablePanels(true);
  assert.equal(saved(), null);
  await s.getState().hydrate(backend);
  assert.equal(s.getState().hydrated, true);
  // Nothing was saved: the in-memory prefs stand.
  assert.equal(s.getState().prefs.customisablePanels, true);
  s.getState().setActivePreset("docked");
  assert.equal(saved()?.activePreset, "docked");
  s.getState().setActivePreset(undefined);
  assert.equal("activePreset" in (saved() ?? {}), false);
});

test("hydrate loads saved prefs through the migration", async () => {
  const s = createLayoutStore();
  const saved = { v: LAYOUT_PREFS_VERSION, activePreset: "docked", customisablePanels: false, docs: {} };
  await s.getState().hydrate(memoryLayoutPersistence(saved));
  assert.equal(s.getState().prefs.activePreset, "docked");
});

test("a backend that fails to load leaves the defaults and still saves later changes", async () => {
  const s = createLayoutStore();
  let saved: unknown = null;
  const warn = console.warn;
  console.warn = () => {};
  try {
    await s.getState().hydrate({
      load: () => Promise.reject(new Error("offline")),
      save: async (p) => {
        saved = p;
      },
    });
  } finally {
    console.warn = warn;
  }
  assert.equal(s.getState().hydrated, true);
  s.getState().setCustomisablePanels(true);
  assert.deepEqual(saved, { v: LAYOUT_PREFS_VERSION, docs: {}, customisablePanels: true });
});

test("prefs of another version, or not prefs at all, start over", () => {
  assert.deepEqual(migrateLayoutPrefs(null), { v: LAYOUT_PREFS_VERSION, docs: {} });
  assert.deepEqual(migrateLayoutPrefs({ v: 99, docs: {} }), { v: LAYOUT_PREFS_VERSION, docs: {} });
  assert.deepEqual(migrateLayoutPrefs({ v: LAYOUT_PREFS_VERSION, docs: {}, activePreset: 3 }), {
    v: LAYOUT_PREFS_VERSION,
    docs: {},
  });
});

test("a stored document passes through the document migration on read", () => {
  const s = createLayoutStore();
  s.setState({
    prefs: { v: LAYOUT_PREFS_VERSION, docs: { p: { v: 1, preset: "p", zones: { gone: { panels: ["x"], active: "x" } }, floats: {}, minimised: [], known: [] } } },
  });
  const d = s.getState().doc("p", ZONES);
  assert.deepEqual(Object.keys(d.zones), ["right"]);
});
