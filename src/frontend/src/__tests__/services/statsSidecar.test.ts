// The take-off an ordinary conversion writes beside its GLB.
//
// `Stats` and `Take-off` were empty for every uploaded file: the take-off existed only for models
// the procedural engine compiled, so the panel had nothing to ask for. The conversion now writes
// the same document at a sibling key, and the rule for that key is stated on both sides -- the
// worker's `converters/keys.stats_sidecar_key` and this one -- because neither can import the
// other's. These pin that they agree.

import assert from "node:assert/strict";
import { test } from "node:test";

const { statsSidecarKey } = await import("@/services/capabilities/rest_capabilities");

test("a GLB's take-off is its key with .stats.json in place of .glb", () => {
  assert.equal(statsSidecarKey("_derived/steel-demo.ifc.glb"), "_derived/steel-demo.ifc.stats.json");
});

test("an engine-suffixed GLB follows the same one rule", () => {
  assert.equal(statsSidecarKey("_derived/model.libtess2.glb"), "_derived/model.libtess2.stats.json");
});

test("a key that is not a GLB still gets a deterministic sibling", () => {
  assert.equal(statsSidecarKey("_derived/model"), "_derived/model.stats.json");
});
