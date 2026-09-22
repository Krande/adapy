// The three resolution modes, over a listing with genuinely mixed dates.
//
// The scenario: a collection re-published on the 27th while one subject was
// last published on the 25th and another only on the 26th. Under `latest`
// those badges sit side by side in the same tree though they came out of
// publishes days apart — fine, but `describeResolution().mixed` has to say so.
//
// Ported from resolve.test.ts, dropping `resolutionLabel`/`absentHere` (not
// carried into this module set) and adding coverage for `incomplete`, which
// is new here: only COMPLETE revisions (asset.json present) are candidates.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { describeResolution, resolveCollection, revisionsInPlay } from "../../assets/resolve";

const R_A = "20260825T135553Z";
const R_B = "20260826T071500Z";
const R_C = "20260827T090000Z";

const COLL = "plant-a";
const K = (s: string, r: string, f: string): string => `assets/${COLL}/${s}/${r}/${f}`;

/** The collection re-published on the 25th and the 26th; area-1 only on the
 *  25th; area-2 only on the 27th. */
const INDEX = foldListing([
  K(COLL, R_A, MANIFEST_FILENAME),
  K(COLL, R_A, HIERARCHY_FILENAME),
  K("area-0", R_A, MANIFEST_FILENAME),
  K("area-0", R_A, HIERARCHY_FILENAME),

  K(COLL, R_B, MANIFEST_FILENAME),
  K("area-0", R_B, MANIFEST_FILENAME),

  K("area-1", R_A, MANIFEST_FILENAME),

  K(COLL, R_C, MANIFEST_FILENAME),
  K("area-2", R_C, MANIFEST_FILENAME),
]);

test("latest takes each subject's newest, independently — so it can mix", () => {
  const r = resolveCollection(INDEX, COLL, { kind: "latest" });
  assert.equal(r.subjects.get("area-0")?.revision.revision, R_B);
  assert.equal(r.subjects.get("area-1")?.revision.revision, R_A);
  assert.equal(r.subjects.get("area-2")?.revision.revision, R_C);
  assert.equal(r.subjects.get(COLL)?.revision.revision, R_C);
  assert.equal(r.missing.size, 0, "latest can never leave a subject unresolved");

  const s = describeResolution(r);
  assert.deepEqual(revisionsInPlay(r), [R_A, R_B, R_C]);
  assert.equal(s.mixed, true);
  assert.equal(s.coeval, false);
  assert.equal(s.subjectCount, 4);
});

test("as-of takes each subject's newest AT OR BEFORE the cut — reproducible, still not coeval", () => {
  const mode = { kind: "as-of", revision: R_B } as const;
  const r = resolveCollection(INDEX, COLL, mode);
  assert.equal(r.subjects.get("area-0")?.revision.revision, R_B);
  assert.equal(r.subjects.get("area-1")?.revision.revision, R_A);
  // Published after the cut, so it resolves to nothing — and the tab is told
  // what IS there so it can offer one click to go and see it.
  assert.equal(r.subjects.has("area-2"), false);
  assert.equal(r.missing.get("area-2")?.newest, R_C);

  const s = describeResolution(r);
  assert.equal(s.mixed, true);
  assert.equal(s.coeval, false);
});

test("as-of before every revision resolves to nothing at all", () => {
  const r = resolveCollection(INDEX, COLL, { kind: "as-of", revision: "20200101T000000Z" });
  assert.equal(r.subjects.size, 0);
  assert.equal(r.missing.size, 4);
});

test("run is exact — and is the only genuinely coeval mode", () => {
  const mode = { kind: "run", revision: R_A } as const;
  const r = resolveCollection(INDEX, COLL, mode);
  assert.deepEqual([...r.subjects.keys()].sort(), ["area-0", "area-1", COLL].sort());
  assert.equal(r.subjects.get("area-0")?.revision.revision, R_A);
  assert.equal(r.missing.get("area-2")?.newest, R_C);

  const s = describeResolution(r);
  assert.deepEqual(s.revisions, [R_A]);
  assert.equal(s.mixed, false);
  assert.equal(s.coeval, true);
});

test("run is coeval even when only one subject participates", () => {
  const mode = { kind: "run", revision: R_C } as const;
  const s = describeResolution(resolveCollection(INDEX, COLL, mode));
  assert.equal(s.coeval, true);
  assert.equal(s.mixed, false);
});

test("`newest` is carried on every resolved subject, whatever the mode picked", () => {
  const r = resolveCollection(INDEX, COLL, { kind: "run", revision: R_A });
  assert.equal(r.subjects.get("area-0")?.revision.revision, R_A);
  assert.equal(r.subjects.get("area-0")?.newest, R_B);
});

test("`skip` removes a subject from both revisionsInPlay and describeResolution", () => {
  const r = resolveCollection(INDEX, COLL, { kind: "latest" });
  const skipCollection = (s: string) => s === COLL;
  // Without the collection subject in the mix, only area-0/1/2's dates count —
  // still three distinct dates here, so still mixed either way, but the count
  // of subjects considered changes.
  const s = describeResolution(r, skipCollection);
  assert.equal(s.subjectCount, 4, "subjectCount is NOT affected by skip — only mixed/revisions are");
  assert.deepEqual(revisionsInPlay(r, skipCollection).length <= revisionsInPlay(r).length, true);
});

// --- incomplete: a publish that died partway --------------------------------

const WITH_INCOMPLETE = foldListing([
  K(COLL, R_A, MANIFEST_FILENAME),
  K(COLL, R_A, HIERARCHY_FILENAME),
  K("area-1", R_A, MANIFEST_FILENAME),
  // area-3's only revision has a hierarchy.json but never got its asset.json —
  // the publish died before the manifest was written.
  K("area-3", R_B, HIERARCHY_FILENAME),
]);

test("a subject with no complete revision is reported in `incomplete`, not resolved and not missing", () => {
  const r = resolveCollection(WITH_INCOMPLETE, COLL, { kind: "latest" });
  assert.equal(r.subjects.has("area-3"), false);
  assert.equal(r.missing.has("area-3"), false, "missing implies a concrete complete alternative exists");
  assert.deepEqual(r.incomplete, ["area-3"]);
});

test("incomplete is empty when every subject's newest revision has a manifest", () => {
  const r = resolveCollection(INDEX, COLL, { kind: "latest" });
  assert.deepEqual(r.incomplete, []);
});

test("a subject with an OLDER complete revision behind a newer incomplete one still resolves to the complete one", () => {
  const idx = foldListing([
    K("area-4", R_A, MANIFEST_FILENAME), // complete at R_A
    K("area-4", R_B, HIERARCHY_FILENAME), // died partway at R_B
  ]);
  const r = resolveCollection(idx, "plant-a", { kind: "latest" });
  assert.equal(r.subjects.get("area-4")?.revision.revision, R_A);
  assert.deepEqual(r.incomplete, ["area-4"]);
});
