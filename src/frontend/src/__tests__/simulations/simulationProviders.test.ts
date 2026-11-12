// The `simulationProviders` slot (plugin API 1.17.0): a plugin's UI for simulation providers.
//
// A plugin registers, per provider id or per domain (the kind of simulation), a parameter form
// section and an "open result" handler. Core's Simulations panel asks the registry who supplies
// each for a given provider; when nobody does it builds the form from the provider's JSON Schema
// and lists the artefacts as downloads. What is pinned here is the registration and that
// resolution: a provider-id match beats a domain match, the two capabilities are resolved on
// their own, and an inactive or disabled plugin supplies neither.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  _versionSatisfies,
  disablePlugin,
  getRegisteredPlugins,
  PLUGIN_API_VERSION,
  registerPlugin,
  resetRegistry,
  resolveSimulationProviderUi,
  type AdaPluginContext,
  type SimulationOpenResultArgs,
} from "@/plugins/registry";

/** A throwaway context: the registry only passes it to predicates here. */
function fakeCtx(): AdaPluginContext {
  return {
    pluginId: "",
    api: { base: "/api", plugin: (id?: string) => `/api/plugins/${id ?? ""}` },
    stores: {} as AdaPluginContext["stores"],
    playback: {} as AdaPluginContext["playback"],
    layers: {} as AdaPluginContext["layers"],
    scene: {
      add() {},
      remove() {},
      requestRender() {},
      paintField() {},
      getActiveFeaMesh: () => null,
      getSelectedFeaRangeIds: () => [],
      setSelectedFeaRanges() {},
      loadModelFromUrl: async () => {},
      unloadModel() {},
      addLines: () => ({remove() {}}),
      addLabels: () => ({remove() {}}),
      hasFeaModel: () => false,
      paintFeaElements: () => null,
      addElementOutlines: () => null,
      isolateFeaElements() {},
      hideFeaElements() {},
      hiddenFeaElements: () => [],
      setFeaElementEdgesVisible() {},
      showColorScale() {},
      setFeaDeformationShown() {},
      setFeaUnpaintedColor() {},
      frameFeaElements: () => false,
      frameBox: () => false,
      frameAll: () => false,
      findFaces: async () => [],
      focusFace: () => false,
      loadModel: async () => {},
      addLineLayer: () => ({setSegments() {}, setPolyline() {}, setLook() {}, remove() {}}),
      addPointLayer: () => ({setPoints() {}, setLook() {}, remove() {}}),
      addImage: () => ({setPixels() {}, setRect() {}, setLook() {}, unavailable: () => null, remove() {}}),
      imageUnavailable: () => null,
    },
    scope: () => "user:me",
    trackJob: () => "",
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
    log() {},
  };
}

const ARM = { id: "arm-sim", domain: "robotics" };
const BEAM = { id: "beam-solver", domain: "fea" };
const form = () => null;
const open = () => {};

beforeEach(() => resetRegistry());

test("the slot is in the plugin API from 1.17.0", () => {
  assert.equal(_versionSatisfies(PLUGIN_API_VERSION, ">=1.17.0 <2.0"), true);
});

test("an entry is namespaced to its plugin, and a plugin without any has an empty list", () => {
  registerPlugin({ id: "alpha", simulationProviders: [{ id: "robots", domain: "robotics", openResult: open }] });
  registerPlugin({ id: "beta" });
  const [alpha, beta] = getRegisteredPlugins();
  assert.deepEqual(alpha.simulationProviders.map((u) => u.id), ["alpha:robots"]);
  assert.deepEqual(beta.simulationProviders, []);
});

test("an entry naming neither a provider nor a domain is ignored", () => {
  registerPlugin({ id: "alpha", simulationProviders: [{ id: "everything", openResult: open }] });
  assert.deepEqual(getRegisteredPlugins()[0].simulationProviders, []);
  assert.deepEqual(resolveSimulationProviderUi(fakeCtx(), ARM), { parameters: null, openResult: null });
});

test("with nothing registered, core's own form and download list are used", () => {
  assert.deepEqual(resolveSimulationProviderUi(fakeCtx(), ARM), { parameters: null, openResult: null });
});

test("a domain entry serves every provider of that kind, and no other kind", () => {
  registerPlugin({
    id: "robots",
    simulationProviders: [{ id: "any", domain: "robotics", renderParameters: form, openResult: open }],
  });
  const arm = resolveSimulationProviderUi(fakeCtx(), ARM);
  assert.equal(arm.parameters?.ui.id, "robots:any");
  assert.equal(arm.openResult?.pluginId, "robots");
  const other = resolveSimulationProviderUi(fakeCtx(), { id: "second-arm-sim", domain: "robotics" });
  assert.equal(other.openResult?.ui.id, "robots:any");
  assert.deepEqual(resolveSimulationProviderUi(fakeCtx(), BEAM), { parameters: null, openResult: null });
});

test("a provider-id entry beats a domain entry, whatever their order", () => {
  registerPlugin({
    id: "robots",
    simulationProviders: [{ id: "any", domain: "robotics", order: -5, renderParameters: form, openResult: open }],
  });
  registerPlugin({
    id: "arm",
    simulationProviders: [{ id: "own", providerId: "arm-sim", order: 99, renderParameters: form, openResult: open }],
  });
  const resolved = resolveSimulationProviderUi(fakeCtx(), ARM);
  assert.equal(resolved.parameters?.ui.id, "arm:own");
  assert.equal(resolved.openResult?.ui.id, "arm:own");
  // Another provider of the domain still gets the domain entry.
  assert.equal(resolveSimulationProviderUi(fakeCtx(), { id: "other", domain: "robotics" }).openResult?.ui.id, "robots:any");
});

test("the form and the open handler are resolved on their own", () => {
  // One plugin knows this provider's parameters; another opens every result of its domain.
  registerPlugin({ id: "arm", simulationProviders: [{ id: "form", providerId: "arm-sim", renderParameters: form }] });
  registerPlugin({ id: "robots", simulationProviders: [{ id: "viewer", domain: "robotics", openResult: open }] });
  const resolved = resolveSimulationProviderUi(fakeCtx(), ARM);
  assert.equal(resolved.parameters?.ui.id, "arm:form");
  assert.equal(resolved.openResult?.ui.id, "robots:viewer");
});

test("an entry naming both a provider and a domain must match both", () => {
  registerPlugin({
    id: "alpha",
    simulationProviders: [{ id: "strict", providerId: "arm-sim", domain: "cfd", openResult: open }],
  });
  assert.equal(resolveSimulationProviderUi(fakeCtx(), ARM).openResult, null);
  assert.equal(resolveSimulationProviderUi(fakeCtx(), { id: "arm-sim", domain: "cfd" }).openResult?.ui.id, "alpha:strict");
});

test("among equal matches the lower order wins, then the id", () => {
  registerPlugin({ id: "beta", simulationProviders: [{ id: "z", domain: "fea", openResult: open }] });
  registerPlugin({ id: "alpha", simulationProviders: [{ id: "late", domain: "fea", order: 10, openResult: open }] });
  registerPlugin({ id: "gamma", simulationProviders: [{ id: "a", domain: "fea", openResult: open }] });
  assert.equal(resolveSimulationProviderUi(fakeCtx(), BEAM).openResult?.ui.id, "beta:z");
});

test("an inactive or disabled plugin supplies nothing", () => {
  registerPlugin({
    id: "dormant",
    activationPredicate: () => false,
    simulationProviders: [{ id: "any", domain: "fea", openResult: open }],
  });
  registerPlugin({ id: "broken", simulationProviders: [{ id: "any", domain: "fea", openResult: open }] });
  assert.equal(resolveSimulationProviderUi(fakeCtx(), BEAM).openResult?.pluginId, "broken");
  disablePlugin("broken", "test");
  assert.equal(resolveSimulationProviderUi(fakeCtx(), BEAM).openResult, null);
});

test("the open handler is handed the run, its scope and the provider", async () => {
  const seen: SimulationOpenResultArgs[] = [];
  registerPlugin({
    id: "robots",
    simulationProviders: [{ id: "viewer", domain: "robotics", openResultLabel: "Play", openResult: (_ctx, args) => void seen.push(args) }],
  });
  const { openResult } = resolveSimulationProviderUi(fakeCtx(), ARM);
  assert.equal(openResult?.ui.openResultLabel, "Play");
  const run = { run_id: "r1", provider: ARM, artefacts: [{ name: "model.glb", role: "model" }] } as never;
  await openResult!.ui.openResult!(fakeCtx(), { run, scope: "user:me", provider: null });
  assert.deepEqual(seen.map((a) => [a.scope, a.run.run_id]), [["user:me", "r1"]]);
});

test("the open-result button says the run is live while it is going, when the plugin names that", async () => {
  const { openResultButtonLabel } = await import("@/plugins/registry");
  const both = { openResultLabel: "Replay", openResultLiveLabel: "Live" };
  assert.equal(openResultButtonLabel(both, true), "Live");
  assert.equal(openResultButtonLabel(both, false), "Replay");
  // Without a live label the button reads the same throughout, as before.
  assert.equal(openResultButtonLabel({ openResultLabel: "Replay" }, true), "Replay");
  assert.equal(openResultButtonLabel({ openResultLiveLabel: "Live" }, false), "Open");
  assert.equal(openResultButtonLabel({}, true), "Open");
});
