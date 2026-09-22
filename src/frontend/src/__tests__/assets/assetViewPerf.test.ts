// Performance shape check over a ~41k-row synthetic spine: parse, merge,
// hierarchy build, view build and flatten should all stay well under a
// generous CI bound. The real target is sub-100ms for each; this test prints
// the actual numbers and only fails on gross regression, since CI runners
// vary a lot in speed.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { buildAssetHierarchy, buildAssetView } from "../../assets/assetView";
import { flattenVisible } from "../../assets/hierarchy";
import { EMPTY_FOREST, mergeSpine } from "../../assets/merge";
import { parseHierarchySlice } from "../../assets/projection";
import type { ManifestSummary } from "../../assets/types";
import { buildSyntheticSpine, SYNTHETIC_ROOT } from "./synthetic";

// Generous, CI-safe bound. The real target is < 100ms per phase; see the
// printed timings for the actual numbers on this machine.
const CI_BOUND_MS = 400;

const COLL = "plant-a";
const REVISION = "20260825T135553Z";
const K = (s: string, r: string, f: string): string => `assets/${COLL}/${s}/${r}/${f}`;

test("a ~41k-row spine parses, merges, builds and flattens within a generous bound", () => {
  const synthetic = buildSyntheticSpine();

  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/${COLL}/${REVISION}`, { provider: "fixture-lines", node: null, delivery: "none", producedAt: REVISION, hierarchyRevision: null }],
    [`${COLL}/${SYNTHETIC_ROOT}/${REVISION}`, { provider: "fixture-lines", node: SYNTHETIC_ROOT, delivery: "none", producedAt: REVISION, hierarchyRevision: null }],
  ]);
  const keys = [
    K(COLL, REVISION, MANIFEST_FILENAME),
    K(COLL, REVISION, HIERARCHY_FILENAME),
    K(SYNTHETIC_ROOT, REVISION, MANIFEST_FILENAME),
    K(SYNTHETIC_ROOT, REVISION, HIERARCHY_FILENAME),
  ];
  for (const leafId of synthetic.sampleLeafIds) {
    keys.push(K(leafId, REVISION, MANIFEST_FILENAME));
    manifests.set(`${COLL}/${leafId}/${REVISION}`, {
      provider: "fixture-lines",
      node: leafId,
      delivery: "mesh",
      producedAt: REVISION,
      hierarchyRevision: null,
    });
  }
  const index = foldListing(keys, manifests);

  const t0 = performance.now();
  const slice = parseHierarchySlice(synthetic.wire);
  const tParse = performance.now();

  const forest = mergeSpine(EMPTY_FOREST, slice.nodes, { subject: SYNTHETIC_ROOT, revision: REVISION, root: SYNTHETIC_ROOT });
  const tMerge = performance.now();

  const hierarchy = buildAssetHierarchy(forest);
  const tHierarchy = performance.now();

  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [REVISION], hierarchy });
  const tView = performance.now();

  const expanded = new Set<string>([SYNTHETIC_ROOT, ...synthetic.branchIds]);
  const rows = flattenVisible(hierarchy, expanded);
  const tFlatten = performance.now();

  const parseMs = tParse - t0;
  const mergeMs = tMerge - tParse;
  const hierarchyMs = tHierarchy - tMerge;
  const viewMs = tView - tHierarchy;
  const flattenMs = tFlatten - tView;
  const expandPathMs = tFlatten - t0;

  // A mode-change re-derive: the hierarchy is already built, only resolution
  // and its downstream passes redo work.
  const tRe0 = performance.now();
  const rerun = buildAssetView({ forest, index, collection: COLL, mode: { kind: "run", revision: REVISION }, indexRevisions: [REVISION], hierarchy });
  const rederiveMs = performance.now() - tRe0;

  console.log(
    `[assetViewPerf] rows=${synthetic.rowCount} parse=${parseMs.toFixed(2)}ms merge=${mergeMs.toFixed(2)}ms ` +
      `hierarchy=${hierarchyMs.toFixed(2)}ms view=${viewMs.toFixed(2)}ms flatten=${flattenMs.toFixed(2)}ms ` +
      `expand-path-total=${expandPathMs.toFixed(2)}ms mode-rederive=${rederiveMs.toFixed(2)}ms`,
  );

  // Sanity: the fixture is the shape it claims to be, and both views resolved.
  assert.equal(slice.nodes.length, synthetic.rowCount);
  assert.equal(forest.nodes.size, synthetic.rowCount);
  assert.ok(rows.length >= 1 + synthetic.branchIds.length);
  assert.ok(view.hierarchy.order.length === synthetic.rowCount);
  assert.equal(rerun.summary.coeval, true);

  assert.ok(expandPathMs < CI_BOUND_MS, `expand path took ${expandPathMs.toFixed(2)}ms, bound is ${CI_BOUND_MS}ms`);
  assert.ok(rederiveMs < CI_BOUND_MS, `mode re-derive took ${rederiveMs.toFixed(2)}ms, bound is ${CI_BOUND_MS}ms`);
});
