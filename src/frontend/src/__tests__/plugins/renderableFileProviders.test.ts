/** The `renderableFileProviders` slot (plugin API 1.5.0): registration, the two
 * ways it is queried, and which provider wins a contested key.
 *
 * The slot exists because a plugin had no way to say "I can turn a file of this
 * kind into something the scene can show" — so a format core has no converter
 * for was unopenable however capable the plugin was, and core's own
 * "can this file go in the scene?" predicate had nobody to ask.
 *
 * Two query paths, deliberately different, and the difference is the thing most
 * likely to be got wrong later:
 *
 *   `isRenderableByPlugin` is context-free and IGNORES activation predicates.
 *   It answers a question about a string, from a render path over a whole file
 *   listing, in modules that hold no scene state.
 *
 *   `getRenderableFileProviders` takes a context and HONOURS them. By the time
 *   a file is being opened there is a context, and a plugin that says it is
 *   inactive should not be handed work.
 *
 * What is not here needs a browser: `openRenderableFile` builds a real plugin
 * context and loads a GLB. The rule it delegates to — `pickRenderableProvider`
 * — is pure and is covered.
 */
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  getRenderableFileProviders,
  getRegisteredPlugins,
  isRenderableByPlugin,
  registerPlugin,
  resetRegistry,
  type AdaPluginContext,
} from "@/plugins/registry";
import { pickRenderableProvider, type ProviderEntry } from "@/plugins/renderableFiles";

/** A throwaway context: the registry only passes it to predicates here. */
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
      setSelectedFeaRanges() {},
      loadModelFromUrl: async () => {},
      unloadModel() {},
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

function entry(pluginId: string, id: string, claims: (k: string) => boolean): ProviderEntry {
  return { pluginId, provider: { id, claims, open: async () => {} } };
}

beforeEach(() => resetRegistry());

test("a provider id is namespaced to its plugin, like every other slot", () => {
  registerPlugin({
    id: "alpha",
    renderableFileProviders: [{ id: "csg", claims: () => true, open: async () => {} }],
  });
  const [plugin] = getRegisteredPlugins();
  assert.deepEqual(
    plugin.renderableFileProviders.map((r) => r.id),
    ["alpha:csg"],
  );
});

test("a plugin registering no providers gets an empty list, not undefined", () => {
  registerPlugin({ id: "alpha" });
  assert.deepEqual(getRegisteredPlugins()[0].renderableFileProviders, []);
  // ...and the context-free query is a constant false, which is the deployed
  // default the predicate has to stay correct for.
  assert.equal(isRenderableByPlugin("anything.db"), false);
});

test("isRenderableByPlugin ignores activation predicates", () => {
  // A plugin that is inactive right now can still RENDER this kind of file —
  // what it can open is a property of the plugin, not of the current scene. A
  // file that came and went from the storage list as the scene changed under it
  // would be a worse failure than offering one whose provider then declines.
  registerPlugin({
    id: "alpha",
    activationPredicate: () => false,
    renderableFileProviders: [
      { id: "csg", claims: (k) => k.endsWith(".db"), open: async () => {} },
    ],
  });
  assert.equal(isRenderableByPlugin("a.db"), true);
  assert.equal(isRenderableByPlugin("a.ifc"), false);
  // The context-taking query is the one that filters.
  assert.deepEqual(getRenderableFileProviders(fakeCtx()), []);
});

test("providers are ordered by (order, id) across plugins", () => {
  registerPlugin({
    id: "beta",
    renderableFileProviders: [{ id: "z", claims: () => true, open: async () => {} }],
  });
  registerPlugin({
    id: "alpha",
    renderableFileProviders: [
      { id: "late", order: 10, claims: () => true, open: async () => {} },
      { id: "early", order: -1, claims: () => true, open: async () => {} },
    ],
  });
  assert.deepEqual(
    getRenderableFileProviders(fakeCtx()).map((e) => e.provider.id),
    ["alpha:early", "beta:z", "alpha:late"],
  );
});

test("the first provider in order that claims the key wins", () => {
  const entries = [
    entry("alpha", "alpha:none", () => false),
    entry("beta", "beta:db", (k) => k.endsWith(".db")),
    entry("gamma", "gamma:all", () => true),
  ];
  assert.equal(pickRenderableProvider(entries, "a.db")?.provider.id, "beta:db");
  // Deterministic on purpose: two plugins claiming one key is a deployment
  // mistake, and it should at least misbehave identically on every machine.
  assert.equal(pickRenderableProvider(entries, "a.ifc")?.provider.id, "gamma:all");
  assert.equal(pickRenderableProvider([], "a.db"), null);
});

test("a throwing claims() does not veto the providers after it", () => {
  const entries = [
    entry("alpha", "alpha:boom", () => {
      throw new Error("boom");
    }),
    entry("beta", "beta:db", (k) => k.endsWith(".db")),
  ];
  assert.equal(pickRenderableProvider(entries, "a.db")?.provider.id, "beta:db");
});

test("a plugin whose claims() threw is disabled and stops claiming", () => {
  registerPlugin({
    id: "alpha",
    renderableFileProviders: [
      {
        id: "csg",
        claims: () => {
          throw new Error("boom");
        },
        open: async () => {},
      },
    ],
  });
  // First call disables the plugin (failure isolation) and answers "no": a
  // predicate that cannot answer must not make the file unopenable.
  assert.equal(isRenderableByPlugin("a.db"), false);
  assert.equal(getRegisteredPlugins()[0].disabled !== undefined, true);
  assert.equal(isRenderableByPlugin("a.db"), false);
});
