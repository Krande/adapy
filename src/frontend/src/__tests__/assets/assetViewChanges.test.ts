// `buildAssetView`'s integration of the change feed (§Decision 4, Phase 4)
// with the rest of the view: root-level `changeState` and per-node
// `evidenceMark` surfacing through `rowFacts`, the "changed by" gate
// (§Decision 6), and -- the thing this file exists to pin -- that STALE
// (`./freshness`) and BEHIND (`./changes`) are independent facts: a row can
// be either, both, or neither, and neither is derived from the other.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { buildAssetView } from "../../assets/assetView";
import type { SourceNodeRow, SourceNodesAnswer } from "../../assets/changes";
import { EMPTY_FOREST, mergeSpine, type Forest } from "../../assets/merge";
import { changeOwners, rowFacts, subjectsByOwner } from "../../assets/rowFacts";
import type { AssetIndex, AssetNode, ManifestSummary } from "../../assets/types";

const R1 = "20260825T000000Z"; // 2026-08-25T00:00:00Z
const R2 = "20260827T000000Z"; // 2026-08-27T00:00:00Z
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

const PROVIDER = "fixture-lines";

function sourceRow(nodeRef: string, lastChangedAt: string, action: SourceNodeRow["action"] = null): SourceNodeRow {
  return { nodeRef, parentRef: null, name: null, lastChangedAt, lastChangedBy: "the-source", observedAt: lastChangedAt, action };
}

function answer(rows: readonly SourceNodeRow[]): SourceNodesAnswer {
  return { source: PROVIDER, rows: new Map(rows.map((r) => [r.nodeRef, r])), unknown: new Set() };
}

/** site-a is republished at R2 without its spine being re-fetched (still
 *  merged at R1) -- the standard staleness shape from `./freshness`. site-b
 *  is published once, at R1, and never re-touched -- never stale. Neither
 *  subject's own manifest carries a `change` record unless a test adds one. */
function fixture(): { forest: Forest; index: AssetIndex } {
  let forest = mergeSpine(EMPTY_FOREST, [an("site-a", null, "site", false), an("leaf-a1", "site-a", "member", true)], {
    subject: "site-a",
    revision: R1,
    root: "site-a",
  });
  forest = mergeSpine(forest, [an("site-b", null, "site", false)], { subject: "site-b", revision: R1, root: "site-b" });

  const manifests = new Map<string, ManifestSummary>([
    [`${COLL}/site-a/${R1}`, { provider: PROVIDER, node: "site-a", delivery: "mesh", producedAt: R1, hierarchyRevision: null, change: null }],
    [`${COLL}/site-a/${R2}`, { provider: PROVIDER, node: "site-a", delivery: "mesh", producedAt: R2, hierarchyRevision: null, change: null }],
    [`${COLL}/site-b/${R1}`, { provider: PROVIDER, node: "site-b", delivery: "mesh", producedAt: R1, hierarchyRevision: null, change: null }],
  ]);
  const index = foldListing(
    [
      K("site-a", R1, MANIFEST_FILENAME),
      K("site-a", R1, HIERARCHY_FILENAME),
      K("site-a", R2, MANIFEST_FILENAME),
      K("site-b", R1, MANIFEST_FILENAME),
      K("site-b", R1, HIERARCHY_FILENAME),
    ],
    manifests,
  );
  return { forest, index };
}

test("a root that is BOTH stale and behind shows both facts, independently and with different vocabulary", () => {
  const { forest, index } = fixture();
  // site-a resolves to R2 (its newest complete revision) but its spine was
  // merged at R1 -- stale. Judged against R2 (its CURRENT resolved revision,
  // not the stale R1), the feed says the source moved even later -- behind.
  const view = buildAssetView({
    forest,
    index,
    collection: COLL,
    mode: { kind: "latest" },
    evidenceAsked: new Set(["site-a", "site-b"]),
    sourceAnswer: new Map([[PROVIDER, answer([sourceRow("site-a", "2026-08-28T00:00:00Z")])]]),
  });
  const facts = rowFacts(view, "site-a")!;
  assert.equal(facts.freshness?.stale, true, "drawn from R1 while the resolution now names R2");
  assert.equal(facts.changeState, "behind", "the source moved after R2, which is what this row is judged against");
  assert.notEqual(String(facts.changeState), "stale", "the two facts must never share a word");
});

test("a root that is stale but NOT behind: independence in the other direction", () => {
  const { forest, index } = fixture();
  const view = buildAssetView({
    forest,
    index,
    collection: COLL,
    mode: { kind: "latest" },
    evidenceAsked: new Set(["site-a"]),
    // Judged against R2 (site-a's current resolution): nothing newer recorded.
    sourceAnswer: new Map([[PROVIDER, answer([sourceRow("site-a", "2026-08-20T00:00:00Z")])]]),
  });
  const facts = rowFacts(view, "site-a")!;
  assert.equal(facts.freshness?.stale, true);
  assert.equal(facts.changeState, "current");
});

test("a root that is behind but NOT stale: never re-published, so freshness has nothing to say", () => {
  const { forest, index } = fixture();
  const view = buildAssetView({
    forest,
    index,
    collection: COLL,
    mode: { kind: "latest" },
    evidenceAsked: new Set(["site-b"]),
    sourceAnswer: new Map([[PROVIDER, answer([sourceRow("site-b", "2026-08-26T00:00:00Z")])]]),
  });
  const facts = rowFacts(view, "site-b")!;
  assert.equal(facts.freshness?.stale, false);
  assert.equal(facts.changeState, "behind");
});

test("a root never asked about has changeState null, not a guessed `not-recorded`", () => {
  const { forest, index } = fixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" } });
  assert.equal(rowFacts(view, "site-a")!.changeState, null);
  assert.equal(view.changes.byRoot.size, 0);
});

test("changeState is null for a row that is not itself a published subject", () => {
  const { forest, index } = fixture();
  const view = buildAssetView({
    forest,
    index,
    collection: COLL,
    mode: { kind: "latest" },
    evidenceAsked: new Set(["site-a", "leaf-a1"]),
    sourceAnswer: new Map([[PROVIDER, answer([sourceRow("site-a", "2026-08-28T00:00:00Z")])]]),
  });
  assert.equal(rowFacts(view, "leaf-a1")!.changeState, null, "leaf-a1 is a node, never a resolved subject of its own");
});

test("evidence marks surface per-node through rowFacts, independent of the root's own changeState", () => {
  const { forest, index } = fixture();
  const changedRows = new Map([["leaf-a1", sourceRow("leaf-a1", "2026-08-27T12:00:00Z", "modified")]]);
  const view = buildAssetView({
    forest,
    index,
    collection: COLL,
    mode: { kind: "latest" },
    evidenceAsked: new Set(["site-a"]),
    sourceAnswer: new Map([[PROVIDER, answer([sourceRow("site-a", "2026-08-20T00:00:00Z")])]]),
    changedRows,
  });
  assert.equal(rowFacts(view, "leaf-a1")!.evidenceMark, "modified");
  assert.equal(rowFacts(view, "site-a")!.evidenceMark, null, "the sweep marked the leaf, not the root itself");
});

test("no manifest carries a `change` -> hasChangeOwners is false and the owner list is empty", () => {
  const { forest, index } = fixture();
  const view = buildAssetView({ forest, index, collection: COLL, mode: { kind: "latest" } });
  assert.equal(view.hasChangeOwners, false);
  assert.deepEqual(changeOwners(view), []);
  assert.deepEqual(subjectsByOwner(view, "alice"), []);
});

test("a manifest with a `change.publishedBy` makes hasChangeOwners true and the actor findable", () => {
  const { forest, index } = fixture();
  const owned = new Map(index.collections.get(COLL)!);
  const siteA = owned.get("site-a")!;
  const revisions = siteA.revisions.map((r) =>
    r.revision === R2 && r.manifest
      ? {
          ...r,
          manifest: {
            ...r.manifest,
            change: { publishedBy: { id: "alice", display: "Alice", application: null }, publishedVia: "user" as const, sourceActor: null, action: null, sourceInstant: null },
          },
        }
      : r,
  );
  owned.set("site-a", { ...siteA, revisions });
  const withOwner = { collections: new Map(index.collections).set(COLL, owned), malformed: index.malformed };

  const view = buildAssetView({ forest, index: withOwner, collection: COLL, mode: { kind: "latest" } });
  assert.equal(view.hasChangeOwners, true);
  const owners = changeOwners(view);
  assert.equal(owners.length, 1);
  assert.equal(owners[0].id, "alice");
  assert.deepEqual(subjectsByOwner(view, "alice"), ["site-a"]);
  assert.equal(rowFacts(view, "site-a")!.changeRecord?.publishedBy?.display, "Alice");
  assert.equal(rowFacts(view, "site-b")!.changeRecord, null);
});
