// An emptied scene puts the camera of the viewport on screen back where the viewer started, whichever
// renderer it is: only the transition into empty, so unloading one of two models keeps the view.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { useModelState } = await import("@/state/modelState");
const { watchCameraHomeOnEmpty } = await import("@/components/viewer/sceneHelpers/cameraHomeOnEmpty");

type Api = Parameters<typeof setActiveViewport>[0];

test("the viewport on screen resets its camera when the last model goes, and only then", () => {
  let resets = 0;
  const release = setActiveViewport({ kind: "native", resetCamera: () => void resets++ } as unknown as Api);
  const ms = useModelState.getState();
  ms.clearLoadedSources();
  const stop = watchCameraHomeOnEmpty();
  try {
    ms.registerLoadedSourceName("a.glb");
    ms.registerLoadedSourceName("b.glb");
    assert.equal(resets, 0, "loading does not reset");
    useModelState.getState().clearLoadedSources();
    assert.equal(resets, 1, "the scene became empty: one reset");
    useModelState.getState().clearLoadedSources();
    assert.equal(resets, 1, "already empty: no second reset");
  } finally {
    stop();
    release();
    useModelState.getState().clearLoadedSources();
  }
});

test("a replace load keeps the camera: the scene is empty only on its way to the next model", async () => {
  const { duringSceneReplace } = await import("@/state/sceneReplace");
  let resets = 0;
  const release = setActiveViewport({ kind: "native", resetCamera: () => void resets++ } as unknown as Api);
  useModelState.getState().clearLoadedSources();
  const stop = watchCameraHomeOnEmpty();
  try {
    useModelState.getState().registerLoadedSourceName("procedural:frame");
    // A recompile: the result replaces itself.
    await duringSceneReplace(async () => {
      useModelState.getState().clearLoadedSources();
      await Promise.resolve();
      useModelState.getState().setLoadedSourceName("procedural:frame");
    });
    assert.equal(resets, 0, "the replaced model came back: the camera stays");
    // A replace that failed after tearing the scene down left it empty after all.
    useModelState.getState().setLoadedSourceName("procedural:frame");
    await assert.rejects(
      duringSceneReplace(async () => {
        useModelState.getState().clearLoadedSources();
        throw new Error("compile failed");
      }),
      /compile failed/,
    );
    assert.equal(resets, 1, "empty when the replace ended: the camera goes home");
  } finally {
    stop();
    release();
    useModelState.getState().clearLoadedSources();
  }
});

test("a replace through loadModel keeps the camera: the new source is registered before the replace ends", async () => {
  // The real load path, not a hand-written replace: loadModel's replace tears the scene down and
  // registers the new source only after the loader returns. With no mounted viewer the three.js
  // loader returns right after the teardown, which is enough to see the order.
  await import("../fixtures/panelRealm");
  const { loadModel } = await import("@/components/viewer/sceneHelpers/loadModel");
  let resets = 0;
  const release = setActiveViewport({ kind: "three", resetCamera: () => void resets++ } as unknown as Api);
  useModelState.getState().clearLoadedSources();
  useModelState.getState().registerLoadedSourceName("other.glb");
  useModelState.getState().registerLoadedSourceName("procedural:frame");
  const stop = watchCameraHomeOnEmpty();
  try {
    // A compile showing its result: a replace, no fit, the current translation reused.
    await loadModel({
      sourceName: "procedural:frame",
      bytes: { from: "url", url: "blob:test" },
      autoFitOverride: false,
      translate: true,
    });
    assert.ok(useModelState.getState().loadedSourceNames.has("procedural:frame"), "the result is loaded");
    assert.equal(resets, 0, "the scene was empty only inside the replace: the camera stays");
  } finally {
    stop();
    release();
    useModelState.getState().clearLoadedSources();
  }
});
