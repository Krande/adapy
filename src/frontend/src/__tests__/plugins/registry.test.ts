import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  _versionSatisfies,
  disablePlugin,
  getPanelsForRegion,
  getRegisteredPlugins,
  getResultSidecarLoaders,
  getSceneColorFieldProviders,
  getUrlParamHandlers,
  PLUGIN_API_VERSION,
  registerPlugin,
  resetRegistry,
  type AdaPluginContext,
} from "@/plugins/registry";

// A throwaway context — the registry only calls predicates/callbacks with it and
// never dereferences the heavy members in these unit tests.
function fakeCtx(): AdaPluginContext {
  return {
    pluginId: "",
    api: { base: "/api", plugin: (id?: string) => `/api/plugins/${id ?? ""}` },
    stores: {} as AdaPluginContext["stores"],
    scene: {
      add() {},
      remove() {},
      requestRender() {},
      paintField() {},
      getActiveFeaMesh: () => null,
      getSelectedFeaRangeIds: () => [],
    },
    scope: () => "user:me",
    log() {},
  };
}

beforeEach(() => resetRegistry());

test("registers a plugin and namespaces its slot ids", () => {
  registerPlugin({
    id: "alpha",
    panels: [{ id: "main", region: "top-panel", render: () => null }],
    sceneColorFields: [
      { id: "uf", label: "UF", supports: "element", resolve: async () => ({ values: new Float32Array(), range: [0, 1] }) },
    ],
    resultSidecarLoaders: [{ id: "load", detect: () => true, load: async () => () => {} }],
  });
  const plugins = getRegisteredPlugins();
  assert.equal(plugins.length, 1);
  assert.equal(plugins[0].id, "alpha");
  assert.equal(plugins[0].panels[0].id, "alpha:main");
  assert.equal(plugins[0].sceneColorFields[0].id, "alpha:uf");
  assert.equal(plugins[0].resultSidecarLoaders[0].id, "alpha:load");
  // apiNamespace defaults to the plugin id.
  assert.equal(plugins[0].apiNamespace, "alpha");
});

test("ignores a duplicate id (first-writer-wins)", () => {
  registerPlugin({ id: "dup", version: "1.0.0" });
  registerPlugin({ id: "dup", version: "2.0.0" });
  const plugins = getRegisteredPlugins();
  assert.equal(plugins.length, 1);
  assert.equal(plugins[0].version, "1.0.0");
});

test("skips a plugin whose coreApiRange excludes this core", () => {
  registerPlugin({ id: "future", coreApiRange: ">=2.0" });
  assert.equal(getRegisteredPlugins().length, 0);
  registerPlugin({ id: "ok", coreApiRange: ">=1.0 <2.0" });
  assert.equal(getRegisteredPlugins().length, 1);
});

test("orders panels within a region by (order, id)", () => {
  registerPlugin({
    id: "z",
    panels: [{ id: "p", region: "fem-sidebar", order: 5, render: () => null }],
  });
  registerPlugin({
    id: "a",
    panels: [
      { id: "late", region: "fem-sidebar", order: 20, render: () => null },
      { id: "early", region: "fem-sidebar", order: 1, render: () => null },
    ],
  });
  const ids = getPanelsForRegion("fem-sidebar", fakeCtx()).map((p) => p.panel.id);
  assert.deepEqual(ids, ["a:early", "z:p", "a:late"]);
});

test("respects whole-plugin and per-slot activation predicates", () => {
  let enabled = false;
  registerPlugin({
    id: "gated",
    activationPredicate: () => enabled,
    panels: [{ id: "p", region: "top-panel", render: () => null }],
  });
  registerPlugin({
    id: "slotgated",
    panels: [
      { id: "p", region: "top-panel", activationPredicate: () => false, render: () => null },
    ],
  });
  assert.equal(getPanelsForRegion("top-panel", fakeCtx()).length, 0);
  enabled = true;
  const ids = getPanelsForRegion("top-panel", fakeCtx()).map((p) => p.panel.id);
  assert.deepEqual(ids, ["gated:p"]); // slotgated's panel still gated off
});

test("failure isolation: a throwing activationPredicate disables the plugin, never throws", () => {
  registerPlugin({
    id: "boom",
    activationPredicate: () => {
      throw new Error("kaboom");
    },
    panels: [{ id: "p", region: "top-panel", render: () => null }],
  });
  registerPlugin({
    id: "fine",
    panels: [{ id: "p", region: "top-panel", render: () => null }],
  });
  // Must not throw, and must still return the healthy plugin's panel.
  const ids = getPanelsForRegion("top-panel", fakeCtx()).map((p) => p.panel.id);
  assert.deepEqual(ids, ["fine:p"]);
  // The throwing plugin is now disabled and contributes nothing further.
  assert.equal(getRegisteredPlugins().find((p) => p.id === "boom")?.disabled !== undefined, true);
});

test("disablePlugin removes a plugin's slots from all queries", () => {
  registerPlugin({
    id: "d",
    sceneColorFields: [
      { id: "f", label: "F", supports: "node", resolve: async () => ({ values: new Float32Array(), range: [0, 1] }) },
    ],
    resultSidecarLoaders: [{ id: "l", detect: () => true, load: async () => () => {} }],
    urlParamHandlers: [{ params: ["x"], handle: async () => true }],
  });
  assert.equal(getSceneColorFieldProviders(fakeCtx()).length, 1);
  assert.equal(getResultSidecarLoaders(fakeCtx()).length, 1);
  assert.equal(getUrlParamHandlers(fakeCtx()).length, 1);
  disablePlugin("d", "test");
  assert.equal(getSceneColorFieldProviders(fakeCtx()).length, 0);
  assert.equal(getResultSidecarLoaders(fakeCtx()).length, 0);
  assert.equal(getUrlParamHandlers(fakeCtx()).length, 0);
});

test("versionSatisfies handles the manifest range grammar", () => {
  assert.equal(_versionSatisfies("1.0.0", ">=1.0 <2.0"), true);
  assert.equal(_versionSatisfies("2.0.0", ">=1.0 <2.0"), false);
  assert.equal(_versionSatisfies("1.5.0", ">=1.0"), true);
  assert.equal(_versionSatisfies("0.9.0", ">=1.0"), false);
  assert.equal(_versionSatisfies(PLUGIN_API_VERSION, undefined), true);
  assert.equal(_versionSatisfies(PLUGIN_API_VERSION, "*"), true);
});
