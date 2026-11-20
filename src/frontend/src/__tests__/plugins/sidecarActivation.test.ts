// A plugin's activation predicate often reads what its result sidecar loader loads (a result
// plugin's panels read its results). Core re-asks placed panels once a loader has
// loaded, and once it is disposed: else a panel that asked during the load stays at "Nothing to
// show here yet" until some unrelated change (a mode switch, a reload) makes it ask again.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { registerPlugin, resetRegistry, pluginActivationVersion } = await import("@/plugins/registry");
const { runResultSidecarLoaders, disposeResultSidecarLoaders } = await import("@/plugins/sidecarLoaders");

test("a placed panel re-asks its predicate once a result sidecar loader has loaded, and when it is disposed", async () => {
  resetRegistry();
  let loaded = false;
  registerPlugin({
    id: "late",
    resultSidecarLoaders: [
      {
        id: "results",
        detect: () => true,
        // Its results arrive after the model change that started the load.
        load: async () => {
          await new Promise((r) => setTimeout(r, 5));
          loaded = true;
          return () => {
            loaded = false;
          };
        },
      },
    ],
  });
  const before = pluginActivationVersion();
  await runResultSidecarLoaders({ manifest: {}, fetcher: {} as never, scope: "shared" });
  assert.ok(loaded);
  assert.ok(pluginActivationVersion() > before, "told once the results are in");
  const afterLoad = pluginActivationVersion();
  disposeResultSidecarLoaders();
  assert.ok(!loaded);
  assert.ok(pluginActivationVersion() > afterLoad, "told when they are gone");
  resetRegistry();
});

test("a result loader belongs to its model: a GLB overlay load leaves a SIN's loader be; removing or replacing the SIN disposes it", async () => {
  const { useModelState } = await import("@/state/modelState");
  const { notifySceneCleared } = await import("@/plugins/sceneCleared");
  const { activeSidecarLoaderCount } = await import("@/plugins/sidecarLoaders");
  resetRegistry();
  disposeResultSidecarLoaders();
  let colours = 0;
  registerPlugin({
    id: "capacity-like",
    resultSidecarLoaders: [
      {
        id: "uf",
        // Its own data is only in an FEA manifest; a GLB load carries none.
        detect: (manifest) => !!(manifest as { plugins?: unknown } | undefined)?.plugins,
        load: async () => {
          colours++;
          return () => {
            colours--;
          };
        },
      },
    ],
  });
  const ms = () => useModelState.getState();
  try {
    ms().registerLoadedSourceName("run.SIN");
    await runResultSidecarLoaders({ manifest: { plugins: {} }, fetcher: {} as never, scope: "shared", sourceName: "run.SIN" });
    assert.equal(colours, 1);
    // The design GLB loaded beside it, as an overlay: no manifest, another source.
    ms().registerLoadedSourceName("design.glb");
    await runResultSidecarLoaders({ manifest: undefined, fetcher: {} as never, scope: "shared", sourceName: "design.glb" });
    assert.equal(colours, 1, "the SIN's usage-factor colours stay");
    assert.equal(activeSidecarLoaderCount(), 1);
    // The GLB goes: still there.
    ms().unregisterLoadedSource("design.glb");
    assert.equal(colours, 1);
    // The SIN loaded again: its own loader is replaced, not doubled.
    await runResultSidecarLoaders({ manifest: { plugins: {} }, fetcher: {} as never, scope: "shared", sourceName: "run.SIN" });
    assert.equal(colours, 1);
    // The SIN leaves the scene: its loader goes with it.
    ms().unregisterLoadedSource("run.SIN");
    assert.equal(colours, 0, "disposed when its source is removed");
    // A replace load clears the scene first: every loader goes.
    ms().registerLoadedSourceName("other.SIN");
    await runResultSidecarLoaders({ manifest: { plugins: {} }, fetcher: {} as never, scope: "shared", sourceName: "other.SIN" });
    assert.equal(colours, 1);
    notifySceneCleared({ reason: "replace", replacing: true, overlaysRemoved: 0 } as never);
    assert.equal(colours, 0, "disposed by the clear a replace load makes");
  } finally {
    ms().clearLoadedSources();
    disposeResultSidecarLoaders();
    resetRegistry();
  }
});
