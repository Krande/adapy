// The row renderer's single entry point: `rowFacts` reduces one `AssetView`
// plus an id to everything one row shows, and `searchRows` is the
// ancestor-closed match set a search box needs to keep a hit visible inside
// a collapsed branch.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { buildAssetView } from "../../assets/assetView";
import { EMPTY_FOREST, mergeSpine } from "../../assets/merge";
import { rowFacts, searchRows } from "../../assets/rowFacts";
import type { AssetNode, ManifestSummary } from "../../assets/types";

const COLL = "plant-a";
const R1 = "20260825T000000Z";
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

// area-1 -> level-2 -> member-3 (published, content), member-4 (unpublished, gap)
function fixture() {
  const forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false), an("level-2", "area-1", "level", false), an("member-3", "level-2", "member", true), an("member-4", "level-2", "member", true)], {
    subject: COLL,
    revision: R1,
    root: null,
  });
  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/member-3/${R1}`, { provider: "fixture-lines", node: "member-3", delivery: "mesh", producedAt: R1, hierarchyRevision: null }],
  ]);
  const index = foldListing(
    [K(COLL, R1, MANIFEST_FILENAME), K(COLL, R1, HIERARCHY_FILENAME), K("member-3", R1, MANIFEST_FILENAME)],
    manifests,
  );
  return buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1] });
}

test("a row rooting content gets a solid badge naming itself", () => {
  const view = fixture();
  const facts = rowFacts(view, "member-3");
  assert.equal(facts?.badge?.weight, "solid");
  assert.equal(facts?.badge?.at, "member-3");
  assert.equal(facts?.badge?.delivery, "mesh");
  assert.equal(facts?.gap, false);
});

test("an ancestor of a published leaf gets a below badge, not solid or ghost", () => {
  const view = fixture();
  const facts = rowFacts(view, "level-2");
  assert.equal(facts?.badge?.weight, "below");
  assert.equal(facts?.badge?.at, "member-3");
});

test("an unpublished sibling leaf is a gap with no badge", () => {
  const view = fixture();
  const facts = rowFacts(view, "member-4");
  assert.equal(facts?.badge, null);
  assert.equal(facts?.gap, true);
  assert.equal(facts?.uncovered, 1);
});

test("an id outside the hierarchy returns null rather than throwing", () => {
  const view = fixture();
  assert.equal(rowFacts(view, "not-a-row"), null);
});

test("dimmed is suppressed for an unexplored branch — absence there is not yet known", () => {
  // A published subtree spine at level-2 has not been fetched: `spineLoaded`
  // omits it, so `unexplored` covers level-2 and its ancestors.
  const forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R1, root: null });
  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/area-1/${R1}`, { provider: "fixture-lines", node: "area-1", delivery: "none", producedAt: R1, hierarchyRevision: null }],
  ]);
  const index = foldListing(
    [K(COLL, R1, MANIFEST_FILENAME), K(COLL, R1, HIERARCHY_FILENAME), K("area-1", R1, MANIFEST_FILENAME), K("area-1", R1, HIERARCHY_FILENAME)],
    manifests,
  );
  const view = buildAssetView({
    forest,
    index,
    collection: COLL,
    mode: { kind: "latest" },
    indexRevisions: [R1],
    spineLoaded: new Map(), // nothing fetched yet
  });
  assert.ok(view.unexplored.has("area-1"));
  assert.equal(rowFacts(view, "area-1")?.dimmed, false);
});

// --- searchRows --------------------------------------------------------------

test("searchRows matches by label or id, case-insensitively, and closes over ancestors", () => {
  const view = fixture();
  const result = searchRows(view.hierarchy, "member-3");
  assert.ok(result);
  assert.equal(result!.matches, 1);
  assert.ok(result!.include.has("member-3"));
  assert.ok(result!.include.has("level-2"), "the ancestor chain is kept so the hit stays reachable");
  assert.ok(result!.include.has("area-1"));
  assert.ok(result!.open.has("level-2"));
  assert.ok(result!.open.has("area-1"));
  assert.equal(result!.open.has("member-3"), false, "the match itself need not be forced open");
});

test("a blank search term is not a search", () => {
  const view = fixture();
  assert.equal(searchRows(view.hierarchy, ""), null);
  assert.equal(searchRows(view.hierarchy, "   "), null);
});

test("no matches still returns an (empty) result, not null", () => {
  const view = fixture();
  const result = searchRows(view.hierarchy, "nothing-matches-this");
  assert.ok(result);
  assert.equal(result!.matches, 0);
  assert.equal(result!.include.size, 0);
});
