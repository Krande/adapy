import assert from "node:assert/strict";
import { test } from "node:test";

// Imported from the concrete modules, not the index: the index picks the
// implementation via `runtime.isRestMode()`, which reads `window`.
import { RESTModelStatsCapability } from "@/services/capabilities/rest_capabilities";
import { WSModelStatsCapability } from "@/services/capabilities/ws_capabilities";
import type { ModelStats } from "@/utils/stats/modelStats";

// The take-off capability is the seam that lets ONE consumer (statsStore) work
// on both comm pipelines. These tests pin the two halves of that contract:
//   - the websocket half sources stats from the GLB the model arrived in,
//   - the REST half keeps its server-sidecar behaviour and refuses embedded
//     stats, so introducing the seam cannot change the hosted viewer.

const STATS: ModelStats = {
  schema_version: 2,
  source_name: "UnitSeparator",
  units: { length: "m", mass: "tonne", area: "m2" },
  objects: 87,
  total_mass: 64.9335,
  total_cog: [11.9371, 5.9314, 2.4335],
  bbox: [24, 12, 5],
  disciplines: [
    { key: "structural", name: "Structural", mass: 63.1952, cog: [12, 6, 2.4], count: 72 },
    { key: "piping", name: "Piping", mass: 1.7383, cog: [11, 5, 3], count: 96 },
    { key: "hvac", name: "HVAC", mass: 0, cog: [0, 0, 0], count: 0 },
    { key: "electrical", name: "Electrical", mass: 0, cog: [0, 0, 0], count: 0 },
  ],
  structural: { mass: 63.1952, beams: [], plates: [] },
  piping: { mass: 1.7383, segments: [], fittings: [] },
  hvac: { mass: 0, segments: [], fittings: [] },
  electrical: { mass: 0, trays: [], cables: [] },
  major_items: [],
};

test("WS capability serves the take-off embedded in the loaded GLB", async () => {
  // No scope/modelId/derivedKey exists on the local `.show()` path — the source
  // is ignored entirely and the answer comes from what the GLB carried.
  const cap = new WSModelStatsCapability();
  assert.equal((await cap.fetchStats({})).available, false, "nothing loaded yet");

  assert.equal(cap.adoptEmbeddedStats(STATS), true, "WS is the transport that sources embedded stats");
  const res = await cap.fetchStats({});
  assert.equal(res.available, true);
  assert.equal(res.stats?.total_mass, 64.9335);
  assert.equal(res.stats?.objects, 87);
});

test("loading a model with no embedded take-off clears the previous one", async () => {
  // Otherwise the panel would keep showing the last model's mass.
  const cap = new WSModelStatsCapability();
  cap.adoptEmbeddedStats(STATS);
  cap.adoptEmbeddedStats(null);
  assert.equal((await cap.fetchStats({})).available, false);
});

test("WS capability advertises no server-rendered export", async () => {
  const cap = new WSModelStatsCapability();
  cap.adoptEmbeddedStats(STATS);
  assert.equal(cap.canExport({}), false, "there is no backend to build the workbook");
});

test("REST capability refuses embedded stats so the sidecar stays the authority", () => {
  const cap = new RESTModelStatsCapability();
  assert.equal(cap.adoptEmbeddedStats(STATS), false);
});

test("REST capability short-circuits an incomplete source without an HTTP call", async () => {
  // A global fetch here would throw / hit the network; not calling it is the
  // assertion.
  const cap = new RESTModelStatsCapability();
  assert.deepEqual(await cap.fetchStats({ scope: null, modelId: null, derivedKey: null }), {
    available: false,
  });
  assert.equal(cap.canExport({ scope: "user:me", modelId: "m", derivedKey: "" }), false);
  assert.equal(cap.canExport({ scope: "user:me", modelId: "m", derivedKey: "k.glb" }), true);
});
