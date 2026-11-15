// The connection builder's annotations (member axes, weld labels) and its clash belong to the
// preview model: when the preview leaves the scene by any route -- a replay clearing the scene, the
// Sources tab, another model replacing it -- they leave with it, rather than float over what comes next.

import assert from "node:assert/strict";
import { test } from "node:test";

const session = await import("@/services/devConnections/previewOnShow");
const { useClashCheckStore } = await import("@/state/clashCheckStore");

const PREVIEW = `${session.PREVIEW_SOURCE_PREFIX}box.box_to_box#3`;

test("the preview's clash goes when the preview is no longer loaded", () => {
  session.setPreviewOnShow(PREVIEW);
  useClashCheckStore.getState().setSource(PREVIEW, PREVIEW);
  // Still loaded: nothing changes.
  session.forgetPreviewIfUnloaded(new Set([PREVIEW, "replay.glb"]));
  assert.equal(useClashCheckStore.getState().sourceKey, PREVIEW);
  // Gone (the scene cleared for a replay): its clash goes too.
  session.forgetPreviewIfUnloaded(new Set(["replay.glb"]));
  assert.notEqual(useClashCheckStore.getState().sourceKey, PREVIEW);
});

test("another model's clash is left alone", () => {
  session.setPreviewOnShow(PREVIEW);
  useClashCheckStore.getState().setSource("model.ifc", "model.ifc");
  session.forgetPreviewIfUnloaded(new Set());
  assert.equal(useClashCheckStore.getState().sourceKey, "model.ifc");
});
