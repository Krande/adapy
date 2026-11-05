import assert from "node:assert/strict";
import { test } from "node:test";

import { createLayoutStore, memoryLayoutPersistence } from "@/layout/layoutStore";
import { LAYOUT_PRESET_CHOICES, resolvePreset } from "@/layout/presetChoice";

// The layout mode: classic unless the user chose docked, each preset with its
// own document.

test("classic is the default, and an unknown saved preset falls back to it", () => {
  assert.deepEqual(LAYOUT_PRESET_CHOICES.map((c) => c.id), ["classic", "docked"]);
  assert.equal(resolvePreset(undefined), "classic");
  assert.equal(resolvePreset("docked"), "docked");
  assert.equal(resolvePreset("a-shell-preset"), "classic");
});

test("the mode and each preset's document are saved apart and survive a load", async () => {
  const backend = memoryLayoutPersistence();
  const store = createLayoutStore();
  await store.getState().hydrate(backend);
  const zones = [{ id: "right", kind: "dock" as const, label: "Right", limits: { min: 1, max: 900, default: 300 } }];
  store.getState().setActivePreset("docked");
  store.getState().update("docked", zones, (d) => ({ ...d, zones: { right: { ...d.zones.right, size: 444 } } }));
  store.getState().update("classic", zones, (d) => ({ ...d, minimised: ["x"] }));
  await Promise.resolve();

  const again = createLayoutStore();
  await again.getState().hydrate(backend);
  assert.equal(again.getState().prefs.activePreset, "docked");
  assert.equal(again.getState().doc("docked", zones).zones.right.size, 444);
  assert.deepEqual(again.getState().doc("docked", zones).minimised, []);
  assert.deepEqual(again.getState().doc("classic", zones).minimised, ["x"]);

  again.getState().setActivePreset(undefined);
  assert.equal(resolvePreset(again.getState().prefs.activePreset), "classic");
});
