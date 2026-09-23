// THE ONE OBJECT THE ASSETS TAB READS — the Phase 2 acceptance for
// `buildAssetView`: orphans, drift, mixed-resolution, freshness, and the
// one-derived-view property (same input in, same badge-relevant output out,
// whether or not the caller precomputed the hierarchy).

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { buildAssetHierarchy, buildAssetView, type AssetView } from "../../assets/assetView";
import { EMPTY_FOREST, mergeSpine, type Forest } from "../../assets/merge";
import type { AssetNode, ManifestSummary } from "../../assets/types";

const R1 = "20260825T000000Z";
const R2 = "20260826T000000Z";
const R3 = "20260827T000000Z";
const COLL = "plant-a";
const K = (s: string, r: string, f: string): string => `assets/${COLL}/${s}/${r}/${f}`;

const an = (id: string, parent: string | null, kind: string, leaf: boolean): AssetNode => ({
  id,
  parent,
  label: id,
  kind,
  leaf,
  delivery: "none",
  provider: "fixture-lines",
});

// ---------------------------------------------------------------------------
// (a) orphans — removed vs. ahead, and the path from `forest.retired`
// ---------------------------------------------------------------------------
//
// Shape: a collection index places area-1 > level-2 at R1. level-2's own
// subtree spine, also fetched at R1, additionally carries member-9. level-2 is
// re-fetched at R2 and no longer carries member-9 — a real removal at the
// source — so member-9 is pruned and retired under level-2. member-9 is
// nonetheless its own published subject (it has its own manifest, published
// at R1, once independently of the row it used to have). member-20 is a
// second published subject that was never merged into any spine at all.

function orphanFixture(): { forest: Forest; index: ReturnType<typeof foldListing> } {
  let forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false), an("level-2", "area-1", "level", false)], {
    subject: COLL,
    revision: R1,
    root: null,
  });
  forest = mergeSpine(forest, [an("level-2", null, "level", false), an("member-9", "level-2", "member", true)], {
    subject: "level-2",
    revision: R1,
    root: "level-2",
  });
  // level-2 re-published without member-9.
  forest = mergeSpine(forest, [an("level-2", null, "level", false), an("member-3", "level-2", "member", true)], {
    subject: "level-2",
    revision: R2,
    root: "level-2",
  });

  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/member-9/${R1}`, { provider: "p", node: "member-9", delivery: "mesh", producedAt: R1, hierarchyRevision: null, change: null }],
    [`${COLL}/member-20/${R3}`, { provider: "p", node: "member-20", delivery: "mesh", producedAt: R3, hierarchyRevision: null, change: null }],
  ]);
  const index = foldListing(
    [
      K(COLL, R1, MANIFEST_FILENAME),
      K(COLL, R1, HIERARCHY_FILENAME),
      K(COLL, R2, MANIFEST_FILENAME),
      K(COLL, R2, HIERARCHY_FILENAME),
      K("level-2", R1, MANIFEST_FILENAME),
      K("level-2", R1, HIERARCHY_FILENAME),
      K("level-2", R2, MANIFEST_FILENAME),
      K("level-2", R2, HIERARCHY_FILENAME),
      K("member-9", R1, MANIFEST_FILENAME),
      K("member-20", R3, MANIFEST_FILENAME),
    ],
    manifests,
  );
  return { forest, index };
}

test("an orphan published BEFORE the newest merged index is `removed`, with its retired path", () => {
  const { forest, index } = orphanFixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2] });
  const orphan = view.orphans.find((o) => o.id === "member-9");
  assert.ok(orphan, "member-9 is a published subject with no row in the forest");
  assert.equal(orphan!.cause, "removed");
  assert.equal(orphan!.revision, R1);
  assert.equal(orphan!.spineRevision, R2);
  assert.deepEqual(orphan!.path, ["area-1", "level-2"], "the path comes from forest.retired, outermost first");
});

test("an orphan published AFTER the newest merged index is `ahead`, with an empty path", () => {
  const { forest, index } = orphanFixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2] });
  const orphan = view.orphans.find((o) => o.id === "member-20");
  assert.ok(orphan);
  assert.equal(orphan!.cause, "ahead");
  assert.deepEqual(orphan!.path, [], "never retired, so there is nothing to report");
});

test("a subject WITH a row in the forest is never listed as an orphan", () => {
  const { forest, index } = orphanFixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2] });
  assert.ok(!view.orphans.some((o) => o.id === "level-2"));
  assert.ok(!view.orphans.some((o) => o.id === "area-1"));
});

// ---------------------------------------------------------------------------
// (b) drift — published against an older collection hierarchy
// ---------------------------------------------------------------------------

function driftFixture(): { forest: Forest; index: ReturnType<typeof foldListing> } {
  let forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R2, root: null });
  forest = mergeSpine(forest, [an("member-3", "area-1", "member", true)], { subject: "member-3", revision: R1, root: null });
  forest = mergeSpine(forest, [an("member-4", "area-1", "member", true)], { subject: "member-4", revision: R1, root: null });

  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/member-3/${R1}`, { provider: "p", node: "member-3", delivery: "mesh", producedAt: R1, hierarchyRevision: R1, change: null }],
    // member-4 recorded the CURRENT collection revision — no drift.
    [`${COLL}/member-4/${R1}`, { provider: "p", node: "member-4", delivery: "mesh", producedAt: R1, hierarchyRevision: R2, change: null }],
  ]);
  const index = foldListing(
    [
      K(COLL, R1, MANIFEST_FILENAME),
      K(COLL, R1, HIERARCHY_FILENAME),
      K(COLL, R2, MANIFEST_FILENAME),
      K(COLL, R2, HIERARCHY_FILENAME),
      K("member-3", R1, MANIFEST_FILENAME),
      K("member-4", R1, MANIFEST_FILENAME),
    ],
    manifests,
  );
  return { forest, index };
}

test("a subject published against an OLDER collection tree is flagged in `drift`", () => {
  const { forest, index } = driftFixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2] });
  assert.deepEqual(view.drift.get("member-3"), { publishedAgainst: R1, shownFrom: R2 });
});

test("a subject published against the CURRENT collection tree is not in `drift`", () => {
  const { forest, index } = driftFixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2] });
  assert.equal(view.drift.has("member-4"), false);
});

// ---------------------------------------------------------------------------
// (c) summary.mixed — two non-collection subjects vs. a collection-only gap
// ---------------------------------------------------------------------------

function mixedFixture(): { forest: Forest; index: ReturnType<typeof foldListing> } {
  let forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R1, root: null });
  forest = mergeSpine(forest, [an("member-3", "area-1", "member", true)], { subject: "member-3", revision: R1, root: null });
  forest = mergeSpine(forest, [an("member-5", "area-1", "member", true)], { subject: "member-5", revision: R2, root: null });
  const index = foldListing([
    K(COLL, R1, MANIFEST_FILENAME),
    K(COLL, R1, HIERARCHY_FILENAME),
    K("member-3", R1, MANIFEST_FILENAME),
    K("member-5", R2, MANIFEST_FILENAME),
  ]);
  return { forest, index };
}

test("two non-collection subjects resolving to different revisions ARE mixed under latest", () => {
  const { forest, index } = mixedFixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1] });
  assert.equal(view.summary.mixed, true);
  assert.deepEqual([...view.summary.revisions], [R1, R2]);
});

test("the same collection is NEVER mixed under run — the only coeval mode", () => {
  const { forest, index } = mixedFixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "run", revision: R1 }, indexRevisions: [] });
  assert.equal(view.summary.mixed, false);
  assert.equal(view.summary.coeval, true);
});

test("a difference that is ONLY the collection subject's own revision does not make the view mixed", () => {
  // The collection subject resolves to R2 (its own newer sweep) while every
  // other subject agrees on R1. `describeResolution` skips the collection
  // subject for exactly this reason.
  let forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R2, root: null });
  forest = mergeSpine(forest, [an("member-3", "area-1", "member", true)], { subject: "member-3", revision: R1, root: null });
  forest = mergeSpine(forest, [an("member-4", "area-1", "member", true)], { subject: "member-4", revision: R1, root: null });
  const index = foldListing([
    K(COLL, R1, MANIFEST_FILENAME),
    K(COLL, R1, HIERARCHY_FILENAME),
    K(COLL, R2, MANIFEST_FILENAME),
    K(COLL, R2, HIERARCHY_FILENAME),
    K("member-3", R1, MANIFEST_FILENAME),
    K("member-4", R1, MANIFEST_FILENAME),
  ]);
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2] });
  assert.equal(view.summary.mixed, false);
  assert.deepEqual([...view.summary.revisions], [R1]);
});

// ---------------------------------------------------------------------------
// (d) freshness — a stale subtree row vs. a current older-index row
// ---------------------------------------------------------------------------

test("a row drawn from a subtree spine is stale once its subject resolves to a newer revision", () => {
  // level-2's subtree was fetched at R1 and never re-fetched; the server has
  // since published level-2 again at R2, so `latest` resolves past it.
  let forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R1, root: null });
  forest = mergeSpine(forest, [an("level-2", null, "level", false), an("member-3", "level-2", "member", true)], {
    subject: "level-2",
    revision: R1,
    root: "level-2",
  });
  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/level-2/${R2}`, { provider: "p", node: "level-2", delivery: "none", producedAt: R2, hierarchyRevision: null, change: null }],
  ]);
  const index = foldListing(
    [
      K("level-2", R1, MANIFEST_FILENAME),
      K("level-2", R1, HIERARCHY_FILENAME),
      K("level-2", R2, MANIFEST_FILENAME),
      K("level-2", R2, HIERARCHY_FILENAME),
    ],
    manifests,
  );
  // Only R1 of the collection index (none here) is "merged" — indexRevisions
  // stays empty, which is fine: this scenario is about the level-2 subtree.
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [] });
  assert.equal(view.freshness.get("level-2")?.stale, true);
  assert.equal(view.freshness.get("member-3")?.stale, true);
  assert.equal(view.staleCount, 2);
});

test("a row from an OLDER collection index the mode still merges is NOT stale, even if the collection has since republished", () => {
  let forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R1, root: null });
  const index = foldListing([
    K(COLL, R1, MANIFEST_FILENAME),
    K(COLL, R1, HIERARCHY_FILENAME),
    K(COLL, R2, MANIFEST_FILENAME),
    K(COLL, R2, HIERARCHY_FILENAME),
  ]);
  // The collection's own newest publish (R2) is NOT in indexRevisions: only R1
  // was actually merged into this forest, and the union model says that is
  // still current, whatever the collection subject resolves to elsewhere.
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1] });
  assert.equal(view.freshness.get("area-1")?.stale, false);
  assert.equal(view.staleCount, 0);
});

// ---------------------------------------------------------------------------
// (e) one derived view — determinism, and hierarchy-precompute equivalence
// ---------------------------------------------------------------------------

function badgeSnapshot(view: AssetView) {
  const coverage = [...view.coverage.byId.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([id, c]) => ({
      id,
      rooted: [...c.rooted].sort(),
      covered: [...c.covered].sort(),
      below: [...c.below].sort(),
      dimmed: c.dimmed,
      gap: c.gap,
    }));
  const orphans = [...view.orphans].sort((a, b) => (a.id < b.id ? -1 : 1));
  const drift = [...view.drift.entries()].sort(([a], [b]) => (a < b ? -1 : 1));
  return { coverage, orphans, drift };
}

test("calling buildAssetView twice on identical input gives deep-equal badge-relevant output", () => {
  const { forest, index } = orphanFixture();
  const input = { forest, index, collection: COLL, mode: { kind: "latest" as const }, indexRevisions: [R1, R2] };
  const first = badgeSnapshot(buildAssetView(input));
  const second = badgeSnapshot(buildAssetView(input));
  assert.deepEqual(first, second);
});

test("passing a prebuilt hierarchy gives the same result as building it internally", () => {
  const { forest, index } = orphanFixture();
  const withoutPrebuilt = badgeSnapshot(
    buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2] }),
  );
  const hierarchy = buildAssetHierarchy(forest);
  const withPrebuilt = badgeSnapshot(
    buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1, R2], hierarchy }),
  );
  assert.deepEqual(withoutPrebuilt, withPrebuilt);
});

test("a mode switch alone re-derives the same shape of output deterministically", () => {
  const { forest, index } = mixedFixture();
  const latest1 = badgeSnapshot(buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1] }));
  const latest2 = badgeSnapshot(buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1] }));
  assert.deepEqual(latest1, latest2);
});
