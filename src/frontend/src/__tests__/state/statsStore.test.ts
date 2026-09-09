import assert from "node:assert/strict";
import { test } from "node:test";

import type { ModelStats } from "@/utils/stats/modelStats";

// End-to-end for the local `assembly.show()` path: adapy embeds the take-off in
// the GLB's asset.extras, the GLB loader hands it to the stats capability, and
// the Stats panel's store must then report `available` WITHOUT any REST backend
// in the picture. Before the capability seam this was structurally impossible —
// statsStore called viewerApi directly and there is no server on that path.
//
// The modules are imported dynamically because the capability index selects its
// implementation via `runtime.isRestMode()`, which reads `window`. Stubbing the
// global first is also what pins WS (non-REST) mode for this test.
(globalThis as any).window = { COMMS_MODE: undefined };

const { capabilities } = await import("@/services/capabilities");
const { useStatsStore } = await import("@/state/statsStore");

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

test("without a REST backend the viewer still selects the WS capability", () => {
  assert.equal(capabilities.transport, "ws");
  assert.equal(capabilities.stats.transport, "ws");
});

test("a GLB-embedded take-off populates the Stats panel store", async () => {
  useStatsStore.getState().clearStats();
  // What setupModelLoader does with asset.extras.model_stats:
  assert.equal(capabilities.stats.adoptEmbeddedStats(STATS), true);
  await useStatsStore.getState().refreshStats();

  const s = useStatsStore.getState();
  assert.equal(s.available, true, "the panel must not say 'take-off not available'");
  assert.equal(s.loading, false);
  assert.equal(s.stats?.total_mass, 64.9335);
  assert.equal(s.stats?.objects, 87);
  // No scope/modelId/derivedKey was ever needed.
  assert.equal(s.derivedKey, null);
});

test("no server means no server-rendered export offered", async () => {
  useStatsStore.getState().clearStats();
  capabilities.stats.adoptEmbeddedStats(STATS);
  await useStatsStore.getState().refreshStats();
  assert.equal(useStatsStore.getState().canExport, false);
  // ...and asking anyway is an inert no-op rather than a stuck spinner.
  await useStatsStore.getState().exportStats("xlsx");
  assert.equal(useStatsStore.getState().exporting, false);
});

test("loading a model without a take-off drops back to 'not available'", async () => {
  capabilities.stats.adoptEmbeddedStats(STATS);
  await useStatsStore.getState().refreshStats();
  assert.equal(useStatsStore.getState().available, true);

  capabilities.stats.adoptEmbeddedStats(null);
  await useStatsStore.getState().refreshStats();
  const s = useStatsStore.getState();
  assert.equal(s.available, false);
  assert.equal(s.stats, null);
});
