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
