// The row renderer's single entry point: `rowFacts` reduces one `AssetView`
// plus an id to everything one row shows, and `searchRows` is the
// ancestor-closed match set a search box needs to keep a hit visible inside
// a collapsed branch.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { buildAssetView } from "../../assets/assetView";
import { EMPTY_FOREST, mergeLevel, mergeSpine } from "../../assets/merge";
import { flattenVisible } from "../../assets/hierarchy";
import { rowFacts, searchRows, shallowestHit } from "../../assets/rowFacts";
import { levelKey } from "../../assets/spines";
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
    [`${COLL}/member-3/${R1}`, { provider: "fixture-lines", node: "member-3", delivery: "mesh", producedAt: R1, hierarchyRevision: null, change: null }],
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
  // area-1's published spine has not been opened: `levelLoaded` does not hold
  // its first level, so `unexplored` covers area-1 and its ancestors.
  const forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R1, root: null });
  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/area-1/${R1}`, { provider: "fixture-lines", node: "area-1", delivery: "none", producedAt: R1, hierarchyRevision: null, change: null }],
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
    levelLoaded: new Set(), // nothing fetched yet
  });
  assert.ok(view.unexplored.has("area-1"));
  assert.equal(rowFacts(view, "area-1")?.dimmed, false);
});

// --- a PARTIALLY opened spine --------------------------------------------------
//
// area-1's spine opened one level: level-2 counts children the tab has not
// fetched, level-5 counts none.
function partial(extra: { loaded?: string[]; levelTwoKids?: AssetNode[] } = {}) {
  let forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R1, root: null });
  forest = mergeLevel(forest, [{ ...an("level-2", "area-1", "level", false), children: 2 }, { ...an("level-5", "area-1", "level", false), children: 0 }], {
    subject: "area-1",
    revision: R1,
    parent: "area-1",
  });
  if (extra.levelTwoKids) forest = mergeLevel(forest, extra.levelTwoKids, { subject: "area-1", revision: R1, parent: "level-2" });
  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/area-1/${R1}`, { provider: "fixture-lines", node: "area-1", delivery: "none", producedAt: R1, hierarchyRevision: null, change: null }],
    [`${COLL}/deep-9/${R1}`, { provider: "fixture-lines", node: "deep-9", delivery: "mesh", producedAt: R1, hierarchyRevision: null, change: null }],
  ]);
  const index = foldListing(
    [
      K(COLL, R1, MANIFEST_FILENAME),
      K(COLL, R1, HIERARCHY_FILENAME),
      K("area-1", R1, MANIFEST_FILENAME),
      K("area-1", R1, HIERARCHY_FILENAME),
      K("deep-9", R1, MANIFEST_FILENAME),
    ],
    manifests,
  );
  const areaLevel = levelKey({ subject: "area-1", revision: R1, node: "area-1" });
  return buildAssetView({
    forest,
    index,
    collection: COLL,
    mode: { kind: "latest" },
    indexRevisions: [R1],
    levelLoaded: new Set([areaLevel, ...(extra.loaded ?? [])]),
  });
}

test("a row whose counted children are not fetched is unexplored: never dimmed, never a gap", () => {
  const view = partial();
  assert.deepEqual(view.unmergedSpines, [], "the spine is open; only a level below is not");
  assert.ok(view.unexplored.has("level-2"));
  assert.ok(view.unexplored.has("area-1"), "and so is everything above it");
  const facts = rowFacts(view, "level-2");
  assert.equal(facts?.dimmed, false);
  assert.equal(facts?.gap, false);
  // A row its spine says is empty is explored: dimmed is a fact there.
  assert.ok(!view.unexplored.has("level-5"));
  assert.equal(rowFacts(view, "level-5")?.dimmed, true);
});

test("a subject not placed while a level is still closed is pending, not an orphan", () => {
  const view = partial();
  assert.deepEqual(view.pending, ["deep-9"]);
  assert.equal(view.orphans.length, 0);
});

test("once every counted level is in, an unplaced subject is an orphan again", () => {
  const kids = [an("member-7", "level-2", "member", true), an("member-8", "level-2", "member", true)];
  const view = partial({ levelTwoKids: kids, loaded: [levelKey({ subject: "area-1", revision: R1, node: "level-2" })] });
  assert.ok(!view.unexplored.has("level-2"));
  assert.deepEqual(view.pending, []);
  assert.deepEqual(
    view.orphans.map((o) => o.id),
    ["deep-9"],
  );
  // The leaves it now holds are payload no publish covers: a real gap.
  assert.equal(rowFacts(view, "level-2")?.gap, true);
});

test("an unopened spine's first level is what `place` would fetch -- one level, not the spine", () => {
  const forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false)], { subject: COLL, revision: R1, root: null });
  const index = foldListing([
    K(COLL, R1, MANIFEST_FILENAME),
    K(COLL, R1, HIERARCHY_FILENAME),
    K("area-1", R1, MANIFEST_FILENAME),
    K("area-1", R1, HIERARCHY_FILENAME),
  ]);
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1], levelLoaded: new Set() });
  assert.deepEqual(view.unmergedSpines, [{ subject: "area-1", revision: R1, node: "area-1" }]);
  assert.deepEqual(view.levelOf("area-1"), { subject: "area-1", revision: R1, node: "area-1" });
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

test("a search lists hits at the top level first, and leaves a matching row closed", () => {
  // area-1 (no hit) -> zone-deck (hit, one level down); area-DECK (hit, top level) -> member-deck (hit)
  const forest = mergeSpine(
    EMPTY_FOREST,
    [
      an("area-1", null, "area", false),
      an("zone-deck", "area-1", "zone", true),
      an("area-DECK", null, "area", false),
      an("member-deck", "area-DECK", "member", true),
    ],
    { subject: COLL, revision: R1, root: null },
  );
  const index = foldListing([K(COLL, R1, MANIFEST_FILENAME), K(COLL, R1, HIERARCHY_FILENAME)], new Map());
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1] });

  const found = searchRows(view.hierarchy, "deck")!;
  assert.equal(found.matches, 3, "case-insensitive: DECK and deck both hit");
  assert.equal(found.open.has("area-DECK"), false, "a matching row is the answer at its level, not opened for the ones below");
  assert.ok(found.open.has("area-1"), "a branch that only holds deeper hits is still opened to show them");

  const depth = shallowestHit(view.hierarchy, found.hits);
  const rows = flattenVisible(view.hierarchy, found.open, { include: found.include, rank: (id) => depth.get(id) ?? Infinity });
  assert.deepEqual(
    rows.map((r) => r.id),
    ["area-DECK", "area-1", "zone-deck"],
    "the top-level hit lists first, although it comes second in the tree",
  );
});
test("a search narrowed by typing on finds exactly what a fresh search finds", () => {
  const view = fixture();
  const fresh = (term: string) => [...(searchRows(buildAssetView({ ...rebuild() }).hierarchy, term)?.hits ?? [])].sort();
  // One hierarchy, queried in the order a user types, deletes and retypes.
  for (const term of ["m", "me", "member", "member-4", "member", "level", "LEVEL-2", "area"]) {
    assert.deepEqual([...(searchRows(view.hierarchy, term)?.hits ?? [])].sort(), fresh(term), term);
  }
});

function rebuild() {
  const forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false), an("level-2", "area-1", "level", false), an("member-3", "level-2", "member", true), an("member-4", "level-2", "member", true)], {
    subject: COLL,
    revision: R1,
    root: null,
  });
  const index = foldListing([K(COLL, R1, MANIFEST_FILENAME), K(COLL, R1, HIERARCHY_FILENAME)], new Map());
  return { forest, index, collection: COLL, mode: { kind: "latest" as const }, indexRevisions: [R1] };
}
test("a node with content from two providers carries a claim per provider, each inherited on its own", () => {
  // area-1 -> level-2 (provider-a claims here) -> member-3 (provider-b claims here), member-4
  const R2 = "20260826T000000Z";
  const forest = mergeSpine(EMPTY_FOREST, [an("area-1", null, "area", false), an("level-2", "area-1", "level", false), an("member-3", "level-2", "member", true), an("member-4", "level-2", "member", true)], {
    subject: COLL,
    revision: R1,
    root: null,
  });
  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/level-2/${R1}`, { provider: "provider-a", node: "level-2", delivery: "build", producedAt: R1, hierarchyRevision: null, change: null }],
    [`${COLL}/member-3/${R2}`, { provider: "provider-b", node: "member-3", delivery: "mesh", producedAt: R2, hierarchyRevision: null, change: null }],
  ]);
  const index = foldListing(
    [K(COLL, R1, MANIFEST_FILENAME), K(COLL, R1, HIERARCHY_FILENAME), K("level-2", R1, MANIFEST_FILENAME), K("member-3", R2, MANIFEST_FILENAME)],
    manifests,
  );
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" }, indexRevisions: [R1] });
  assert.deepEqual(view.contentProviders, ["provider-a", "provider-b"]);

  const member3 = rowFacts(view, "member-3")!;
  assert.deepEqual(
    member3.claims.map((c) => [c.provider, c.weight, c.at, c.revision]),
    [
      ["provider-a", "ghost", "level-2", R1],
      ["provider-b", "solid", "member-3", R2],
    ],
    "the covering provider-a publish is not hidden by provider-b's own",
  );
  assert.equal(member3.badge?.provider, "provider-b", "the any-provider badge is the nearest publish, and says whose");

  assert.deepEqual(rowFacts(view, "member-4")!.claims.map((c) => [c.provider, c.weight]), [["provider-a", "ghost"]]);
  assert.deepEqual(
    rowFacts(view, "level-2")!.claims.map((c) => [c.provider, c.weight]),
    [
      ["provider-a", "solid"],
      ["provider-b", "below"],
    ],
  );
});