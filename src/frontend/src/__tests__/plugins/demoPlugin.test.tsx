import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  getPanelsForRegion,
  getResultSidecarLoaders,
  getSceneColorFieldProviders,
  getUrlParamHandlers,
  getRegisteredPlugins,
  resetRegistry,
  type AdaPluginContext,
} from "@/plugins/registry";
import { register as registerDemo } from "@adapy-plugins/demo";

// Enable the demo plugin for the whole test file (it is dormant by default).
(globalThis as Record<string, unknown>).__ADA_DEMO_PLUGIN__ = true;

function fakeCtx(pluginId = ""): AdaPluginContext {
  const logs: string[] = [];
  return {
    pluginId,
    api: { base: "/api", plugin: (id?: string) => `/api/plugins/${id ?? pluginId}` },
    stores: {} as AdaPluginContext["stores"],
    scene: { add() {}, remove() {}, requestRender() {}, paintField() {}, getActiveFeaMesh: () => null, getSelectedFeaRangeIds: () => [] },
    scope: () => "user:me",
    theme: {
      bg: "#111827",
      surface: "#1f2937",
      border: "#374151",
      text: "#f3f4f6",
      textMuted: "#9ca3af",
      accent: "#3b82f6",
      pass: "#22c55e",
      warn: "#f59e0b",
      fail: "#ef4444",
    },
    log: (_l, m) => logs.push(m),
  };
}

beforeEach(() => {
  resetRegistry();
  registerDemo();
});

test("demo plugin registers into every slot", () => {
  const [demo] = getRegisteredPlugins();
  assert.equal(demo.id, "demo");
  assert.ok(demo.panels.length >= 2, "has panels");
  assert.ok(demo.sceneColorFields.length >= 1, "has a colour field");
  assert.ok(demo.resultSidecarLoaders.length >= 1, "has a sidecar loader");
  assert.ok(demo.urlParamHandlers.length >= 1, "has a url handler");
});

test("demo panel mounts into both the top-panel and fem-sidebar regions", () => {
  const top = getPanelsForRegion("top-panel", fakeCtx());
  const fem = getPanelsForRegion("fem-sidebar", fakeCtx());
  assert.deepEqual(top.map((p) => p.panel.id), ["demo:hello"]);
  assert.deepEqual(fem.map((p) => p.panel.id), ["demo:fem"]);
  // The top-panel entry carries a top-bar button.
  assert.ok(top[0].panel.topBarButton, "top panel exposes a top-bar button");
  assert.equal(top[0].panel.topBarButton?.label, "Demo plugin");
  // The render callback returns a React element (does not throw).
  const el = top[0].panel.render(fakeCtx("demo"));
  assert.ok(el && typeof el === "object", "panel render returns a node");
});

test("demo colour field resolves trivial values through the sceneColorFields slot", async () => {
  const providers = getSceneColorFieldProviders(fakeCtx());
  assert.deepEqual(providers.map((p) => p.provider.id), ["demo:uf"]);
  const result = await providers[0].provider.resolve(fakeCtx("demo"), { field: "demo:uf" });
  assert.ok(result.values instanceof Float32Array);
  assert.equal((result.values as Float32Array).length, 3);
  assert.deepEqual(result.range, [0, 1]);
  assert.deepEqual(result.colorFor?.(0), [0, 0, 1]);
});

test("demo sidecar loader detects + returns a disposer", async () => {
  const loaders = getResultSidecarLoaders(fakeCtx());
  assert.deepEqual(loaders.map((l) => l.loader.id), ["demo:hello-loader"]);
  const loader = loaders[0].loader;
  assert.equal(loader.detect(undefined), true); // enabled -> detects even w/o manifest
  const dispose = await loader.load(fakeCtx("demo"), {
    manifest: undefined,
    fetcher: { json: async () => ({}), bytes: async () => new ArrayBuffer(0), url: (k) => k },
    scope: "user:me",
  });
  assert.equal(typeof dispose, "function");
  dispose(); // must not throw
});

test("demo url handler consumes its param through the urlParamHandlers slot", async () => {
  const handlers = getUrlParamHandlers(fakeCtx());
  assert.deepEqual(handlers.map((h) => h.handler.params), [["demoParam"]]);
  const took = await handlers[0].handler.handle(fakeCtx("demo"), { demoParam: "1" });
  assert.equal(took, true);
});

test("demo plugin is dormant when not enabled", () => {
  (globalThis as Record<string, unknown>).__ADA_DEMO_PLUGIN__ = false;
  try {
    assert.equal(getPanelsForRegion("top-panel", fakeCtx()).length, 0);
    assert.equal(getSceneColorFieldProviders(fakeCtx()).length, 0);
  } finally {
    (globalThis as Record<string, unknown>).__ADA_DEMO_PLUGIN__ = true;
  }
});
