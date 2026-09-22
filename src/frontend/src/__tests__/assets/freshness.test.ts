// Two "is this row out of date" facts, kept apart on purpose.
//
// STALE: the shape of the tree on screen no longer matches the resolution its
// badges are read from. DRIFT: a subject was published against an older
// collection hierarchy than the one on screen now.
//
// Ported from freshness.test.ts. `nodeFreshness` now takes `revisionOf` PER
// ORIGIN (subject + the revision it was fetched at), not per subject — one
// subject can legitimately contribute rows at several revisions. Drift tests
// are new here.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  hierarchyDrift,
  nodeFreshness,
  staleCount,
  type NodeOrigin,
} from "../../assets/freshness";

const R1 = "20260825T135553Z";
const R2 = "20260827T090000Z";

const origins = (entries: Array<[string, string, string]>): Map<string, NodeOrigin> =>
  new Map(entries.map(([id, subject, revision]) => [id, { subject, revision }]));

test("a row is fresh when its spine is the revision the resolution names", () => {
  const f = nodeFreshness(origins([["n-1", "area-a", R1]]), () => R1);
  assert.equal(f.get("n-1")?.stale, false);
  assert.equal(f.get("n-1")?.shownAt, R1);
  assert.equal(f.get("n-1")?.resolvedAt, R1);
});

test("a row is stale when the resolution has moved to another revision", () => {
  // The mode switch: badges re-point at R2, the already-fetched rows do not.
  const f = nodeFreshness(origins([["n-1", "area-a", R1]]), () => R2);
  assert.equal(f.get("n-1")?.stale, true);
  assert.equal(f.get("n-1")?.shownAt, R1);
  assert.equal(f.get("n-1")?.resolvedAt, R2);
});

test("a subject the mode resolves to nothing is NOT stale", () => {
  // Under `run` and `as-of` a subject that did not participate is simply
  // absent. The tab says that in its own words; calling it staleness would
  // put a second, wronger sentence beside it.
  const f = nodeFreshness(origins([["n-1", "area-a", R1]]), () => null);
  assert.equal(f.get("n-1")?.stale, false);
  assert.equal(f.get("n-1")?.resolvedAt, null);
});

test("staleness is per subject — one branch can be stale while another is current", () => {
  const f = nodeFreshness(
    origins([
      ["n-1", "area-a", R1],
      ["n-2", "area-a", R1],
      ["n-3", "area-b", R2],
    ]),
    (origin) => (origin.subject === "area-a" ? R2 : R2),
  );
  assert.equal(f.get("n-1")?.stale, true);
  assert.equal(f.get("n-2")?.stale, true);
  assert.equal(f.get("n-3")?.stale, false);
  assert.equal(staleCount(f), 2);
});

test("the resolution lookup is memoised per origin, not asked once per row", () => {
  // One spine is up to ~41k rows against a single origin.
  const many: Array<[string, string, string]> = [];
  for (let i = 0; i < 5_000; i++) many.push([`n-${i}`, "area-a", R1]);
  let calls = 0;
  const f = nodeFreshness(origins(many), () => {
    calls++;
    return R1;
  });
  assert.equal(f.size, 5_000);
  assert.equal(calls, 1);
});

test("two different origins (same subject, different fetch revision) are memoised separately", () => {
  const entries = origins([
    ["n-1", "area-a", R1],
    ["n-2", "area-a", R2],
  ]);
  const seen: string[] = [];
  const f = nodeFreshness(entries, (origin) => {
    seen.push(origin.revision);
    return origin.revision;
  });
  assert.deepEqual(seen.sort(), [R1, R2]);
  assert.equal(f.get("n-1")?.stale, false);
  assert.equal(f.get("n-2")?.stale, false);
});

test("a node absent from the newer spine keeps its old revision and shows as stale", () => {
  // THE DELETION CASE, spelled out. A-B-C was fetched at R1. The branch is
  // re-published at R2 as A-B only. A union merge sets A and B to R2 and
  // CANNOT remove C — absence is not a delete instruction unless the document
  // claims completeness for that subject. So C survives, still stamped R1,
  // and that disagreement is the only honest signal available.
  const merged = origins([
    ["A", "area-a", R1],
    ["B", "area-a", R1],
    ["C", "area-a", R1],
  ]);
  // The R2 spine arrives carrying A and B only.
  for (const id of ["A", "B"]) merged.set(id, { subject: "area-a", revision: R2 });

  const f = nodeFreshness(merged, () => R2);
  assert.equal(f.get("A")?.stale, false);
  assert.equal(f.get("B")?.stale, false);
  assert.equal(f.get("C")?.stale, true, "C outlived the publish that produced it");
  assert.equal(f.get("C")?.shownAt, R1);
  assert.equal(staleCount(f), 1);
});

test("staleCount is zero on an untouched forest", () => {
  assert.equal(staleCount(nodeFreshness(new Map(), () => R1)), 0);
});

// --- hierarchyDrift ----------------------------------------------------------

test("drift is reported when a subject was published against an OLDER collection tree", () => {
  const d = hierarchyDrift(R1, R2);
  assert.deepEqual(d, { publishedAgainst: R1, shownFrom: R2 });
});

test("no drift when the recorded revision equals what's on screen", () => {
  assert.equal(hierarchyDrift(R1, R1), null);
});

test("no drift when the subject's manifest recorded NO hierarchy_revision", () => {
  assert.equal(hierarchyDrift(null, R2), null);
});

test("no drift when there is no collection index on screen at all", () => {
  assert.equal(hierarchyDrift(R1, null), null);
});

test("no drift when the recorded revision is NEWER than what's on screen — that is `ahead`, not drift", () => {
  assert.equal(hierarchyDrift(R2, R1), null);
});
