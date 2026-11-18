// The Orthographic view command: it flips the one projection setting, shows its state, and is
// offered disabled, with the viewport's own reason, on a viewport that has no orthographic view.
// Every viewport has one now, the three.js-free one included.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

await import("../fixtures/panelRealm");

const { coreCommandSpecs } = await import("@/layout/coreCommands");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { followProjection, useProjectionStore } = await import("@/state/projectionStore");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { createNativeViewportApi } = await import("@/viewport/native/nativeViewportApi");
type Api = import("@/viewport/api/viewportApi").ViewportApi;
type Scene = import("@/viewport/native/nativeScene").NativeScene;

const command = () => coreCommandSpecs().find((s) => s.id === ID.orthographic)!;

afterEach(() => useProjectionStore.getState().setOrthographic(false));

test("the command toggles the projection and shows it as checked", () => {
  const release = setActiveViewport({ kind: "three", unavailable: () => null } as unknown as Api);
  try {
    const spec = command();
    assert.equal(spec.disabledReason?.() ?? null, null, "the three.js viewport draws it");
    assert.equal(spec.checked?.(), false);
    spec.run();
    assert.equal(useProjectionStore.getState().orthographic, true);
    assert.equal(spec.checked?.(), true);
    spec.run();
    assert.equal(useProjectionStore.getState().orthographic, false);
  } finally {
    release();
  }
});

test("the command carries a viewport's reason when it has no orthographic view", () => {
  const reason = "no orthographic view here";
  const release = setActiveViewport({ kind: "three", unavailable: (f: string) => (f === "orthographic" ? reason : null) } as unknown as Api);
  try {
    assert.equal(command().disabledReason?.(), reason);
  } finally {
    release();
  }
});

test("the three.js-free viewport has an orthographic view: the command is offered there", () => {
  const api = createNativeViewportApi({} as unknown as Scene);
  assert.equal(api.unavailable("orthographic"), null);
  assert.equal(api.unavailable("fit-selection"), null);
  const release = setActiveViewport(api);
  try {
    assert.equal(command().disabledReason?.() ?? null, null);
  } finally {
    release();
  }
});

test("a viewport following the projection is told now and on each change, not on an unchanged set", () => {
  const seen: boolean[] = [];
  const stop = followProjection((on) => seen.push(on));
  useProjectionStore.getState().toggleOrthographic();
  useProjectionStore.getState().setOrthographic(true);
  useProjectionStore.getState().setOrthographic(false);
  stop();
  useProjectionStore.getState().setOrthographic(true);
  assert.deepEqual(seen, [false, true, false]);
});
