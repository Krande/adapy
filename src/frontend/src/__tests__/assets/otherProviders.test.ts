// "Load from another provider": which Sources node a row of a loaded model is, and who else
// publishes geometry for it.

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { forestNodeFor, providerAlternatives } from "../../assets/otherProviders";
import type { Forest } from "../../assets/merge";
import type { AssetIndex, AssetNode, AssetRevision, ManifestSummary } from "../../assets/types";

const node = (id: string, parent: string | null, label: string): AssetNode =>
  ({ id, parent, label, kind: "", leaf: false, delivery: "none", provider: "p" }) as AssetNode;

// site -> zone -> {beam-1 "/BEAM", beam-2 "/BEAM" (a repeated label), plate "/PLATE"}
const forest: Forest = {
  nodes: new Map(
    [
      node("site", null, "/SITE"),
      node("zone", "site", "/ZONE"),
      node("beam-1", "zone", "/BEAM"),
      node("beam-2", "zone", "/BEAM"),
      node("plate", "zone", "/PLATE"),
    ].map((n) => [n.id, n]),
  ),
  origins: new Map(),
  retired: new Map(),
  namedBy: new Map(),
};

test("a row is found by walking down its names; a level the tree lacks is stepped over", () => {
  assert.equal(forestNodeFor(forest, "site", []), "site", "the model root is its node");
  assert.equal(forestNodeFor(forest, "site", ["/ZONE", "/PLATE"]), "plate");
  assert.equal(forestNodeFor(forest, "site", ["/ZONE", "builder group", "/PLATE"]), "plate");
  assert.equal(forestNodeFor(forest, "site", ["/ZONE", "/BEAM [beam-2]"]), "beam-2", "a tagged id settles a repeated label");
  assert.equal(forestNodeFor(forest, "site", ["/ZONE", "/BEAM"]), null, "an ambiguous label is not guessed");
  assert.equal(forestNodeFor(forest, "site", ["/ZONE", "/NOPE"]), null);
  assert.equal(forestNodeFor(forest, "elsewhere", ["/ZONE"]), null);
});

const rev = (revision: string, provider: string, delivery: ManifestSummary["delivery"], leaves?: number): AssetRevision => ({
  revision,
  files: new Set(),
  manifest: { provider, node: null, delivery, producedAt: revision, hierarchyRevision: null, change: null, leaves },
  manifestError: null,
});

const index = (subjects: Record<string, AssetRevision[]>): AssetIndex => ({
  collections: new Map([
    ["c", new Map(Object.entries(subjects).map(([s, revisions]) => [s, { collection: "c", subject: s, revisions }]))],
  ]),
  malformed: [],
});

test("other providers' geometry: nearest claim first, newest manifest decides, the loaded provider excluded", () => {
  const idx = index({
    site: [rev("r1", "mesher", "mesh"), rev("r1", "builder", "build", 10)],
    zone: [rev("r2", "builder", "build", 4)],
  });
  const alts = providerAlternatives(idx, forest, "c", "plate", "builder");
  assert.deepEqual(alts, [{ provider: "mesher", subject: "site", revision: "r1", node: "plate" }]);
  // From the other side: the builder's claim at the zone is nearer than the one at the site.
  assert.deepEqual(providerAlternatives(idx, forest, "c", "plate", "mesher"), [
    { provider: "builder", subject: "zone", revision: "r2", node: "plate" },
  ]);
});

test("a provider whose newest publish there draws nothing is not offered", () => {
  const idx = index({ site: [rev("r1", "mesher", "mesh"), rev("r2", "mesher", "none"), rev("r3", "builder", "build", 0)] });
  assert.deepEqual(providerAlternatives(idx, forest, "c", "plate", "other"), []);
  assert.deepEqual(providerAlternatives(null, forest, "c", "plate", "other"), []);
});
