// A compile/recompile result replaces the scene with `translate: true` so the model does not
// re-centre under a still camera. `clearScene` always wipes the cached translation
// (clear_scene.ts), so `replaceModelNow` must capture it before the teardown and put it back
// after, for `setupModelLoaderAsync`'s `reuseCurrent: translate` to have anything to reuse.
//
// This only exercises the capture/restore around `clearScene`: with no mounted viewer,
// `replaceModelNow` logs "No scene found" and returns right after that, before the GLTF load.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

// Answers vite's `?worker` imports (the FEA streaming teardown, pulled in by clearScene).
await import("../fixtures/panelRealm");

const { replace_model } = await import("@/utils/scene/handlers/update_scene_from_message");
const { useModelState } = await import("@/state/modelState");

test("a replace with translate:true keeps the scene's translation across the teardown", async () => {
  useModelState.getState().setTranslation(new THREE.Vector3(1, 2, 3));
  await replace_model({ url: "blob:test", sourceName: "a.glb", translate: true });
  const kept = useModelState.getState().translation;
  assert.ok(kept, "the translation survives clearScene");
  assert.deepEqual(kept!.toArray(), [1, 2, 3]);
});

test("a replace without translate re-centres: clearScene's wipe is left in place", async () => {
  useModelState.getState().setTranslation(new THREE.Vector3(4, 5, 6));
  await replace_model({ url: "blob:test", sourceName: "a.glb" });
  assert.equal(useModelState.getState().translation, null);
});

test("a replace with translate:true but nothing cached yet does not invent one", async () => {
  useModelState.setState({ translation: null });
  await replace_model({ url: "blob:test", sourceName: "a.glb", translate: true });
  assert.equal(useModelState.getState().translation, null);
});
