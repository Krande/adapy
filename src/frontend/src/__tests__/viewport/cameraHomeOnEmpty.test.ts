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
