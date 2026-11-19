// A clear of the scene takes what plugins drew over it (plugin API 1.30.0): their lines, labels and
// line, point and image layers, through every trigger, since every trigger is `clearScene`
// (clearSceneOnePath.test.ts). A handle kept to something taken stays safe to call and does nothing.
// A replace leaves them. And `ctx.scene.onSceneCleared` tells a plugin, once per clear.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import type { ViewportApi } from "@/viewport/api/viewportApi";
import type { SceneCleared } from "@/plugins/registry";

// Answers vite's `?worker` imports (the FEA streaming teardown imports one).
await import("../fixtures/panelRealm");

const { clearScene } = await import("@/utils/scene/handlers/clear_scene");
const { addLineLayer, addPointLayer, addImage, makePluginPlayback } = await import("@/plugins/sceneLayers");
const { addLines, addLabels, removeOwnerOverlays } = await import("@/plugins/sceneOverlays");
const { onSceneCleared } = await import("@/plugins/sceneCleared");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { playbackClock } = await import("@/state/playbackClock");
const { resetLayerRegistry } = await import("@/state/layerRegistry");

(globalThis as { localStorage?: unknown }).localStorage = undefined;

/** A viewport whose layers count what they hold, and throw when used once disposed, as a real
 *  layer's freed GPU buffers might. */
function strictViewport() {
  const live = { drawn: 0, layers: 0, calls: 0 };
  const layer = () => {
    live.layers++;
    let disposed = false;
    const use = <T>(value: T): T => {
      if (disposed) throw new Error("layer used after dispose");
      live.calls++;
      return value;
    };
    return {
      addLines: () => use(++live.drawn),
      addPoints: () => use(++live.drawn),
      add: () => use(++live.drawn),
      update: () => use(undefined),
      setGeometry: () => use(undefined),
      setPositions: () => use(undefined),
      setLook: () => use(undefined),
      setPixels: () => use(undefined),
      setRect: () => use(undefined),
      unavailable: () => use(null),
      remove: () => void use(live.drawn--),
      clear: () => {},
      dispose: () => {
        if (disposed) throw new Error("disposed twice");
        disposed = true;
        live.layers--;
      },
    };
  };
  const viewport = {
    kind: "native",
    overlays: layer,
    images: layer,
    billboards: layer,
    clearModels: () => {},
    setObjectMarks: () => {},
    hide: () => {},
    show: () => {},
  } as unknown as ViewportApi;
  return { viewport, live };
}

let withdraw: (() => void) | null = null;
const OWNERS = ["p", "q"];

afterEach(() => {
  for (const owner of OWNERS) removeOwnerOverlays(owner);
  withdraw?.();
  withdraw = null;
  resetLayerRegistry();
});

/** Two plugins' drawings: every kind of handle `ctx.scene.add*` returns. */
function drawEverything() {
  const lines = addLineLayer("p", { segments: [0, 0, 0, 1, 0, 0], layer: { id: "path", label: "Path" } });
  const points = addPointLayer("p", { positions: [0, 0, 0], colors: [1, 0, 0] });
  const image = addImage("q", { origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: new Uint8Array(4) });
  const once = addLines("q", { segments: [0, 0, 0, 0, 1, 0] });
  const labels = addLabels("q", [{ position: [0, 0, 0], text: "A" }]);
  return { lines, points, image, once, labels };
}

test("a clear removes every plugin's lines, points, images and labels, and their handles do nothing after", async () => {
  const { viewport, live } = strictViewport();
  withdraw = setActiveViewport(viewport);
  const { lines, points, image, once, labels } = drawEverything();
  const track = makePluginPlayback("p").addTrack({ id: "run", duration: 10, show: () => {} });
  assert.equal(live.drawn, 5);
  assert.ok(live.layers > 0);

  await clearScene({ reason: "test" });
  assert.equal(live.drawn, 0, "nothing a plugin drew is left");
  assert.equal(live.layers, 0, "and the viewport's layers they were drawn in are disposed");

  // The plugins still hold their handles: every call is a no-op, the layers are not touched.
  const calls = live.calls;
  assert.doesNotThrow(() => {
    lines.setSegments(new Float32Array(6));
    lines.setPolyline(new Float32Array(9));
    lines.setLook({ visible: false, color: "#ff0000" });
    points.setPoints(new Float32Array(3), 1, new Float32Array(3));
    points.setLook({ opacity: 0.5 });
    image.setPixels(new Uint8Array(16), 2, 2);
    image.setRect({ origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0] });
    image.setLook({ visible: true });
    assert.equal(image.unavailable(), null);
    for (const h of [lines, points, image, once, labels]) h.remove();
  });
  assert.equal(live.calls, calls, "a removed handle reaches no layer");
  assert.equal(live.drawn, 0, "and draws nothing again");

  // What is not drawn over the scene stays the plugin's: its track on the playback clock.
  assert.equal(playbackClock.state().tracks, 1, "the playback track stays");
  track.remove();
  assert.equal(playbackClock.state().tracks, 0);

  // A layer made after the clear is drawn as before.
  const again = addLineLayer("p", { segments: [0, 0, 0, 1, 0, 0] });
  assert.equal(live.drawn, 1);
  again.setSegments(new Float32Array(12));
  again.remove();
  assert.equal(live.drawn, 0);
});

test("a replace leaves the plugins' drawings, and says so", async () => {
  const { viewport, live } = strictViewport();
  withdraw = setActiveViewport(viewport);
  const { lines } = drawEverything();
  const told: SceneCleared[] = [];
  const off = onSceneCleared((c) => told.push(c));
  try {
    await clearScene({ reason: "replace", replacing: true, keepQueuedLoads: true, keepProcedural: true });
    assert.equal(live.drawn, 5, "a replace is a load: what the plugins drew stays");
    assert.doesNotThrow(() => lines.setSegments(new Float32Array(6)));
    assert.deepEqual(told, [{ reason: "replace", replacing: true, overlaysRemoved: false }]);
  } finally {
    off();
  }
});

test("onSceneCleared is told once per clear, whatever asked, until unsubscribed", async () => {
  const { viewport } = strictViewport();
  withdraw = setActiveViewport(viewport);
  const told: SceneCleared[] = [];
  const off = onSceneCleared((c) => told.push(c));
  // One that throws does not keep the others from being told, nor the clear from finishing.
  const offBad = onSceneCleared(() => {
    throw new Error("a plugin's bug");
  });
  const quiet = console.error;
  console.error = () => {};
  try {
    // The option sets the triggers clear with (clearSceneOnePath.test.ts lists the triggers).
    const triggers = [
      {}, // the storage browser's Clear, Remove all
      { reason: "plugin" }, // a plugin's ctx.scene.clearScene
      { reason: "scope change" },
      { reason: "gallery", keepQueuedLoads: true, keepProcedural: true },
      { reason: "re-convert", keepQueuedLoads: true, keepProcedural: true },
      { reason: "unload streamed result", keepQueuedLoads: true, keepProcedural: true },
      { reason: "clear_loaded_model", keepQueuedLoads: true, keepProcedural: true },
      { reason: "replace", replacing: true, keepQueuedLoads: true, keepProcedural: true },
    ];
    for (const options of triggers) await clearScene(options);
    assert.equal(told.length, triggers.length, "once per clear");
    assert.deepEqual(
      told.map((c) => [c.reason, c.replacing, c.overlaysRemoved]),
      triggers.map((o) => [o.reason ?? null, !!o.replacing, !o.replacing]),
    );
  } finally {
    console.error = quiet;
    offBad();
  }
  off();
  await clearScene({ reason: "test" });
  assert.equal(told.length, 8, "an unsubscribed listener is not told");
});

test("a listener that draws again draws into the scene as cleared", async () => {
  const { viewport, live } = strictViewport();
  withdraw = setActiveViewport(viewport);
  addLineLayer("p", { segments: [0, 0, 0, 1, 0, 0] });
  const off = onSceneCleared(() => void addLineLayer("p", { segments: [0, 0, 0, 0, 0, 1] }));
  try {
    await clearScene({ reason: "test" });
    assert.equal(live.drawn, 1, "the old path went, the new one stays");
  } finally {
    off();
  }
});
