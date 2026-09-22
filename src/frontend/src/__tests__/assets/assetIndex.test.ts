// The folded asset index: collection -> subject -> revision -> files.
//
// `indexFromWire` (the production path) and `foldListing` (the test oracle
// over raw keys) must agree on the same data. Also pins: `_staging` keys are
// skipped, a malformed key is reported rather than thrown, `defaultCollection`
// picks the newest publish rather than the alphabetically first collection,
// and the manifest-summary mapping from the wire's snake_case.
//
// Also ports `projectIndexes.test.ts`'s coverage of what is now
// `collectionIndexRevisions`: the reported failure was that reading only the
// newest collection index made every other root vanish behind whichever
// publish happened last.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  collectionIndexRevisions,
  compareRevisions,
  defaultCollection,
  foldListing,
  indexFromWire,
  isComplete,
  MANIFEST_FILENAME,
  HIERARCHY_FILENAME,
  revisionsOf,
  subjectsOf,
} from "../../assets/assetIndex";
import { resolveCollection } from "../../assets/resolve";
import type { ManifestSummary, WireAssetIndex } from "../../assets/types";

const R_A = "20260825T135553Z";
const R_B = "20260826T071500Z";
const R_C = "20260827T090000Z";

const COLL = "plant-a";

// --- indexFromWire vs foldListing agree ------------------------------------

function wireDoc(): WireAssetIndex {
  return {
    collections: {
      [COLL]: [
        {
          subject: COLL,
          // Wire order is NEWEST FIRST — the fold must still land ascending.
          revisions: [
            { revision: R_B, files: [MANIFEST_FILENAME, HIERARCHY_FILENAME] },
            { revision: R_A, files: [MANIFEST_FILENAME, HIERARCHY_FILENAME] },
          ],
        },
        {
          subject: "area-1",
          revisions: [
            {
              revision: R_A,
              files: [MANIFEST_FILENAME, "mesh.glb"],
              manifest: {
                provider: "fixture-lines",
                node: "area-1",
                delivery: "mesh",
                produced_at: R_A,
                hierarchy_revision: R_A,
              },
            },
          ],
        },
      ],
    },
    malformed: [{ key: "assets/plant-a/bad key", reason: "invalid segment" }],
  };
}

function equivalentKeysAndManifests(): { keys: string[]; manifests: Map<string, ManifestSummary> } {
  const keys = [
    `assets/${COLL}/${COLL}/${R_A}/${MANIFEST_FILENAME}`,
    `assets/${COLL}/${COLL}/${R_A}/${HIERARCHY_FILENAME}`,
    `assets/${COLL}/${COLL}/${R_B}/${MANIFEST_FILENAME}`,
    `assets/${COLL}/${COLL}/${R_B}/${HIERARCHY_FILENAME}`,
    `assets/${COLL}/area-1/${R_A}/${MANIFEST_FILENAME}`,
    `assets/${COLL}/area-1/${R_A}/mesh.glb`,
  ];
  const manifests = new Map<string, ManifestSummary>([
    [
      `${COLL}/area-1/${R_A}`,
      { provider: "fixture-lines", node: "area-1", delivery: "mesh", producedAt: R_A, hierarchyRevision: R_A },
    ],
  ]);
  return { keys, manifests };
}

test("indexFromWire and foldListing agree on the same data", () => {
  const fromWire = indexFromWire(wireDoc());
  const { keys, manifests } = equivalentKeysAndManifests();
  const folded = foldListing(keys, manifests);

  assert.deepEqual(revisionsOf(fromWire, COLL), revisionsOf(folded, COLL));
  const wireSubject = subjectsOf(fromWire, COLL).get("area-1");
  const foldedSubject = subjectsOf(folded, COLL).get("area-1");
  assert.deepEqual(
    wireSubject?.revisions.map((r) => [r.revision, [...r.files].sort(), r.manifest]),
    foldedSubject?.revisions.map((r) => [r.revision, [...r.files].sort(), r.manifest]),
  );
});

test("wire revisions arrive newest-first; the folded model is ascending either way", () => {
  const fromWire = indexFromWire(wireDoc());
  const collRevs = subjectsOf(fromWire, COLL).get(COLL)?.revisions.map((r) => r.revision);
  assert.deepEqual(collRevs, [R_A, R_B]);
});

// --- _staging is skipped, malformed keys reported --------------------------

test("_staging keys never enter the index", () => {
  const idx = foldListing([`assets/_staging/upload-1/source.bin`, `assets/${COLL}/area-1/${R_A}/${MANIFEST_FILENAME}`]);
  assert.equal(idx.collections.size, 1);
  assert.ok(!idx.collections.has("_staging"));
});

test("a malformed key is reported, not thrown, and the rest of the listing still folds", () => {
  const idx = foldListing([
    `assets/${COLL}/area-1/${R_A}/${MANIFEST_FILENAME}`,
    `assets/bad`, // wrong arity
    `assets/${COLL}/-bad-leading-dash/${R_A}/${MANIFEST_FILENAME}`,
  ]);
  assert.equal(idx.malformed.length, 2);
  assert.ok(subjectsOf(idx, COLL).has("area-1"));
});

test("indexFromWire carries the server's malformed list through as keys", () => {
  const idx = indexFromWire(wireDoc());
  assert.deepEqual(idx.malformed, ["assets/plant-a/bad key"]);
});

// --- defaultCollection picks the newest publish -----------------------------

test("defaultCollection opens on the newest publish, not the alphabetically first collection", () => {
  const idx = foldListing([
    `assets/aaa-older/area-1/${R_A}/${MANIFEST_FILENAME}`,
    `assets/zzz-newer/area-1/${R_C}/${MANIFEST_FILENAME}`,
  ]);
  assert.equal(defaultCollection(idx), "zzz-newer");
});

test("defaultCollection is null for an empty or absent index", () => {
  assert.equal(defaultCollection(null), null);
  assert.equal(defaultCollection(foldListing([])), null);
});

// --- manifest summary mapping ----------------------------------------------

test("the wire's snake_case manifest fields map to camelCase", () => {
  const idx = indexFromWire(wireDoc());
  const rev = subjectsOf(idx, COLL).get("area-1")?.revisions[0];
  assert.deepEqual(rev?.manifest, {
    provider: "fixture-lines",
    node: "area-1",
    delivery: "mesh",
    producedAt: R_A,
    hierarchyRevision: R_A,
  });
});

test("a missing hierarchy_revision on the wire maps to null, not undefined", () => {
  const idx = indexFromWire({
    collections: {
      [COLL]: [
        {
          subject: "area-1",
          revisions: [
            {
              revision: R_A,
              files: [MANIFEST_FILENAME],
              manifest: { provider: "fixture-lines", node: "area-1", delivery: "mesh", produced_at: R_A },
            },
          ],
        },
      ],
    },
    malformed: [],
  });
  const rev = subjectsOf(idx, COLL).get("area-1")?.revisions[0];
  assert.equal(rev?.manifest?.hierarchyRevision, null);
});

test("a revision with no manifest at all maps to manifest: null", () => {
  const idx = indexFromWire({
    collections: {
      [COLL]: [{ subject: "area-2", revisions: [{ revision: R_A, files: [MANIFEST_FILENAME] }] }],
    },
    malformed: [],
  });
  const rev = subjectsOf(idx, COLL).get("area-2")?.revisions[0];
  assert.equal(rev?.manifest, null);
  assert.equal(rev?.manifestError, null);
});

test("a manifest_error on the wire is carried through", () => {
  const idx = indexFromWire({
    collections: {
      [COLL]: [
        {
          subject: "area-2",
          revisions: [{ revision: R_A, files: [MANIFEST_FILENAME], manifest_error: "bad json" }],
        },
      ],
    },
    malformed: [],
  });
  assert.equal(subjectsOf(idx, COLL).get("area-2")?.revisions[0].manifestError, "bad json");
});

// --- isComplete --------------------------------------------------------------

test("a revision is complete once it has the manifest filename", () => {
  const idx = foldListing([`assets/${COLL}/area-1/${R_A}/${MANIFEST_FILENAME}`]);
  const rev = subjectsOf(idx, COLL).get("area-1")?.revisions[0];
  assert.ok(rev);
  assert.equal(isComplete(rev!), true);
});

test("a revision missing the manifest is incomplete — a publish that died partway", () => {
  const idx = foldListing([`assets/${COLL}/area-1/${R_A}/mesh.glb`]);
  const rev = subjectsOf(idx, COLL).get("area-1")?.revisions[0];
  assert.ok(rev);
  assert.equal(isComplete(rev!), false);
});

// --- collectionIndexRevisions (ported from projectIndexes.test.ts) ---------

const K = (subject: string, rev: string, file: string): string => `assets/${COLL}/${subject}/${rev}/${file}`;

/** Every collection-level publish writes its own manifest + hierarchy.json;
 *  two of them (B, C) also carry a leaf publish of their own. */
const SWEEP = R_A; // the wide, hierarchy-only collection sweep
const RUN_B = R_B; // a later publish naming one subject
const RUN_C = R_C; // and another

function threeIndexRevisions(): string[] {
  return [
    K(COLL, SWEEP, MANIFEST_FILENAME),
    K(COLL, SWEEP, HIERARCHY_FILENAME),
    K(COLL, RUN_B, MANIFEST_FILENAME),
    K(COLL, RUN_B, HIERARCHY_FILENAME),
    K("area-1", RUN_B, MANIFEST_FILENAME),
    K(COLL, RUN_C, MANIFEST_FILENAME),
    K(COLL, RUN_C, HIERARCHY_FILENAME),
    K("area-2", RUN_C, MANIFEST_FILENAME),
  ];
}

test("latest reads EVERY collection index, so no earlier root vanishes behind a newer publish", () => {
  const idx = foldListing(threeIndexRevisions());
  const revs = collectionIndexRevisions(idx, COLL, { kind: "latest" }).map((r) => r.revision);
  assert.deepEqual(revs, [SWEEP, RUN_B, RUN_C]);
});

test("they come back oldest first, so the newest row wins a shared node on merge", () => {
  const idx = foldListing(threeIndexRevisions());
  const revs = collectionIndexRevisions(idx, COLL, { kind: "latest" }).map((r) => r.revision);
  assert.deepEqual(revs, [...revs].sort(compareRevisions));
});

test("run reads exactly one index — coeval means that publish and nothing else", () => {
  const idx = foldListing(threeIndexRevisions());
  const revs = collectionIndexRevisions(idx, COLL, { kind: "run", revision: RUN_B }).map((r) => r.revision);
  assert.deepEqual(revs, [RUN_B]);
});

test("as-of reads every index up to its instant", () => {
  const idx = foldListing(threeIndexRevisions());
  assert.deepEqual(
    collectionIndexRevisions(idx, COLL, { kind: "as-of", revision: RUN_B }).map((r) => r.revision),
    [SWEEP, RUN_B],
  );
  assert.deepEqual(
    collectionIndexRevisions(idx, COLL, { kind: "as-of", revision: SWEEP }).map((r) => r.revision),
    [SWEEP],
  );
});

test("a collection revision missing hierarchy.json is skipped, not fetched as a hole", () => {
  const idx = foldListing([
    K(COLL, SWEEP, MANIFEST_FILENAME),
    K(COLL, SWEEP, HIERARCHY_FILENAME),
    K(COLL, RUN_B, MANIFEST_FILENAME), // no hierarchy.json at this revision
  ]);
  assert.deepEqual(
    collectionIndexRevisions(idx, COLL, { kind: "latest" }).map((r) => r.revision),
    [SWEEP],
  );
});

test("a collection revision missing asset.json (incomplete publish) is skipped too", () => {
  const idx = foldListing([
    K(COLL, SWEEP, MANIFEST_FILENAME),
    K(COLL, SWEEP, HIERARCHY_FILENAME),
    K(COLL, RUN_B, HIERARCHY_FILENAME), // no asset.json — died partway
  ]);
  assert.deepEqual(
    collectionIndexRevisions(idx, COLL, { kind: "latest" }).map((r) => r.revision),
    [SWEEP],
  );
});

test("an unknown collection, or no index, is empty rather than an error", () => {
  const idx = foldListing(threeIndexRevisions());
  assert.deepEqual(collectionIndexRevisions(idx, "nope", { kind: "latest" }), []);
  assert.deepEqual(collectionIndexRevisions(null, COLL, { kind: "latest" }), []);
});

test("under run, a subject from another publish resolves to nothing — the lens", () => {
  const idx = foldListing([
    K(COLL, SWEEP, MANIFEST_FILENAME),
    K(COLL, SWEEP, HIERARCHY_FILENAME),
    K("area-1", SWEEP, MANIFEST_FILENAME),
    K("area-1", SWEEP, HIERARCHY_FILENAME),
    K("area-2", RUN_B, MANIFEST_FILENAME),
    K("area-2", RUN_B, HIERARCHY_FILENAME),
  ]);
  const run = resolveCollection(idx, COLL, { kind: "run", revision: RUN_B });
  assert.equal(run.subjects.has("area-2"), true, "the run's own subject resolves");
  assert.equal(
    run.subjects.has("area-1"),
    false,
    "a subject the run does not name resolves to NOTHING — which is what the lens hides",
  );

  const latest = resolveCollection(idx, COLL, { kind: "latest" });
  assert.equal(latest.subjects.has("area-1"), true);
  assert.equal(latest.subjects.has("area-2"), true);
});
