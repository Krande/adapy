// A HIERARCHY SWEEP MUST NOT SHADOW THE CONTENT UNDER IT.
//
// The fault this pins, ported from the original plugin's regression test. A
// collection sweep writes a manifest at every root it covers and no delivery
// claim anywhere, because it is a hierarchy-only publish. If resolution picked
// one revision per subject — the newest — the sweep would become every
// subject's answer and a genuinely delivered publish underneath it would be
// shadowed. `resolveCollection`'s second pick (`content`, gated by
// `carriesContent`) is what keeps the badge alive: one resolution, same mode,
// same bound, answering two questions that were always different.
//
// "Carries content" here is `assetView.ts`'s `carriesContent`: the resolved
// revision's manifest summary has `delivery !== "none"`.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { buildAssetHierarchy, buildAssetView, carriesContent, ROLE_CONTENT, ROLE_TREE } from "../../assets/assetView";
import { EMPTY_FOREST, mergeSpine } from "../../assets/merge";
import { resolveCollection } from "../../assets/resolve";
import type { AssetNode, ManifestSummary } from "../../assets/types";

const COLL = "plant-a";
const CONTENT_RUN = "20260825T135553Z"; // a real delivery of area-1's mesh
const SWEEP = "20260825T235800Z"; // a later hierarchy-only sweep touching area-1 too

const K = (s: string, r: string, f: string): string => `assets/${COLL}/${s}/${r}/${f}`;

const INDEX = foldListing(
  [
    K(COLL, CONTENT_RUN, MANIFEST_FILENAME),
    K(COLL, CONTENT_RUN, HIERARCHY_FILENAME),
    K("area-1", CONTENT_RUN, MANIFEST_FILENAME),
    K("area-1", CONTENT_RUN, HIERARCHY_FILENAME),

    K(COLL, SWEEP, MANIFEST_FILENAME),
    K(COLL, SWEEP, HIERARCHY_FILENAME),
    K("area-1", SWEEP, MANIFEST_FILENAME),
    K("area-1", SWEEP, HIERARCHY_FILENAME),
  ],
  new Map<string, ManifestSummary>([
    [`${COLL}/${COLL}/${CONTENT_RUN}`, { provider: "fixture-lines", node: null, delivery: "none", producedAt: CONTENT_RUN, hierarchyRevision: null, change: null }],
    [`${COLL}/area-1/${CONTENT_RUN}`, { provider: "fixture-lines", node: "area-1", delivery: "mesh", producedAt: CONTENT_RUN, hierarchyRevision: null, change: null }],
    [`${COLL}/${COLL}/${SWEEP}`, { provider: "fixture-lines", node: null, delivery: "none", producedAt: SWEEP, hierarchyRevision: null, change: null }],
    [`${COLL}/area-1/${SWEEP}`, { provider: "fixture-lines", node: "area-1", delivery: "none", producedAt: SWEEP, hierarchyRevision: null, change: null }],
  ]),
);

// -- resolution ---------------------------------------------------------------

test("a subject resolves twice: its newest revision, and its newest revision WITH content", () => {
  const res = resolveCollection(INDEX, COLL, { kind: "latest" }, { carriesContent });
  const area1 = res.subjects.get("area-1");
  assert.equal(area1?.revision.revision, SWEEP, "the newest revision is still the sweep");
  assert.equal(area1?.content?.revision, CONTENT_RUN, "and the content publish is still found");
});

test("with no content predicate the second pick IS the first, not null", () => {
  // Callers that do not make the distinction must be unaffected by its
  // existence — otherwise adding the field is a silent behaviour change.
  const res = resolveCollection(INDEX, COLL, { kind: "latest" });
  const area1 = res.subjects.get("area-1");
  assert.equal(area1?.content, area1?.revision);
});

test("the content pick stays inside the bound the mode draws", () => {
  // `run` is the only coeval mode, and it has to stay that way: a run that
  // published no content answers "none" rather than reaching back to an
  // earlier publish that was not part of it.
  const res = resolveCollection(INDEX, COLL, { kind: "run", revision: SWEEP }, { carriesContent });
  const area1 = res.subjects.get("area-1");
  assert.equal(area1?.revision.revision, SWEEP);
  assert.equal(area1?.content, null, "the sweep is not coeval with the earlier content publish");
});

test("as-of cuts the content pick too — it does not reach past the cut", () => {
  const res = resolveCollection(INDEX, COLL, { kind: "as-of", revision: "20260101T000000Z" }, { carriesContent });
  assert.equal(res.subjects.get("area-1"), undefined);
  assert.equal(res.missing.get("area-1")?.newest, SWEEP);
});

// -- the badge the sweep must not erase ---------------------------------------

const NODES: readonly AssetNode[] = [
  { id: "area-1", parent: null, label: "Area 1", kind: "area", leaf: false, delivery: "none", provider: "fixture-lines" },
  { id: "level-2", parent: "area-1", label: "Level 2", kind: "level", leaf: false, delivery: "none", provider: "fixture-lines" },
  { id: "member-3", parent: "level-2", label: "Member 3", kind: "member", leaf: true, delivery: "none", provider: "fixture-lines" },
];

function view(mode: Parameters<typeof buildAssetView>[0]["mode"] = { kind: "latest" }) {
  const forest = mergeSpine(EMPTY_FOREST, NODES, { subject: "area-1", revision: SWEEP, root: "area-1" });
  const hierarchy = buildAssetHierarchy(forest);
  const indexRevisions = ["20260825T000000Z" /* an older sweep index too */, SWEEP].filter((r) =>
    // Only the ones actually merged into the index would be passed in
    // production; here the mode's own bound decides what counts.
    mode.kind !== "run" ? true : r === mode.revision,
  );
  return buildAssetView({ forest, index: INDEX, collection: COLL, mode, hierarchy, indexRevisions });
}

test("the sweep does not take the content badge off the area it swept", () => {
  const cov = view().coverage.byId.get("area-1");
  assert.ok(cov, "the area is in the tree");
  assert.ok(cov!.rooted.has(ROLE_CONTENT), "content is still rooted at the area");
  assert.ok(cov!.rooted.has(ROLE_TREE), "and the sweep's own tree-role still is too");
});

test("the content badge propagates to a descendant as a ghost, not lost", () => {
  const cov = view().coverage.byId.get("member-3");
  assert.ok(cov!.covered.has(ROLE_CONTENT));
  assert.equal(cov!.coveredBy.get(ROLE_CONTENT), "area-1");
});

test("under the sweep's own run there is genuinely no content — and that is correct", () => {
  const cov = view({ kind: "run", revision: SWEEP }).coverage.byId.get("area-1");
  assert.equal(cov!.rooted.has(ROLE_CONTENT), false, "a run lens must not manufacture content that run did not publish");
  assert.equal(cov!.rooted.has(ROLE_TREE), true);
});
