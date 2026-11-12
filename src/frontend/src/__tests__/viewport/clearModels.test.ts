// Clear / Remove all goes through the viewport on screen, whichever renderer it is: the storage
// browser's Clear left the three.js-free viewport's models on screen when the teardown only knew the
// three.js scene.

import assert from "node:assert/strict";
import { test } from "node:test";

// Answers vite's `?worker` imports (the FEA streaming teardown imports one).
await import("../fixtures/panelRealm");

const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { clear_loaded_model } = await import("@/utils/scene/handlers/clear_loaded_model");
const { getViewerRuntime } = await import("@/state/viewerRuntime");

type Api = Parameters<typeof setActiveViewport>[0];

test("clearing the scene clears the models of the viewport on screen", async () => {
  let cleared = 0;
  const viewport = { kind: "native", clearModels: () => void cleared++ } as unknown as Api;
  const release = setActiveViewport(viewport);
  try {
    await clear_loaded_model();
    assert.equal(cleared, 1);
  } finally {
    release();
  }
});

test("clearing the scene also empties the three.js scene's model groups, whichever viewport is on screen", async () => {
  const removed: unknown[] = [];
  const group = { clear() {}, traverse() {}, children: [] };
  const runtime = getViewerRuntime();
  const keyMap = new Map<string, unknown>([["m1", group]]);
  const before = { scene: runtime.scene.current, keyMap: runtime.modelKeyMap.current };
  (runtime.scene as { current: unknown }).current = { remove: (g: unknown) => void removed.push(g) };
  (runtime.modelKeyMap as { current: unknown }).current = keyMap;
  const release = setActiveViewport({ kind: "native", clearModels() {} } as unknown as Api);
  try {
    await clear_loaded_model();
    assert.deepEqual(removed, [group], "a group kept from before a renderer switch is removed");
    assert.equal(keyMap.size, 0);
  } finally {
    release();
    (runtime.scene as { current: unknown }).current = before.scene;
    (runtime.modelKeyMap as { current: unknown }).current = before.keyMap;
  }
});
