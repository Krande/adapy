// The forest merge: a union that is allowed to delete, but only inside a
// document that claims completeness for a subject.
//
// Ported from merge.test.ts, driven through the store there — here through
// the pure `mergeSpine(forest, incoming, {subject, revision, root})` this
// module set exposes instead, plus new coverage for `retired` (which the
// store version did not expose directly) and for the null-parent guard that
// keeps a subtree document's self-declared, parent-less root from re-parenting
// a node the forest already places.

import assert from "node:assert/strict";
import { test } from "node:test";

import { EMPTY_FOREST, mergeSpine, type Forest } from "../../assets/merge";
import type { AssetNode } from "../../assets/types";

const R1 = "20260825T135553Z";
const R2 = "20260827T090000Z";
const AREA = "area-a";

const node = (id: string, parent: string | null): AssetNode => ({
  id,
  parent,
  label: id,
  kind: "level",
  leaf: false,
  delivery: "none",
  provider: "fixture-lines",
});

/** A-B-C, one chain, published as AREA's subtree at R1. */
const chain = [node("A", null), node("B", "A"), node("C", "B")];

const ids = (f: Forest): string[] => [...f.nodes.keys()].sort();

test("a subtree merge prunes what its newer revision no longer carries", () => {
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  assert.deepEqual(ids(forest), ["A", "B", "C"]);

  // C was removed at the source; the R2 publish of the same root carries A-B only.
  forest = mergeSpine(forest, [node("A", null), node("B", "A")], { subject: AREA, revision: R2, root: "A" });
  assert.deepEqual(ids(forest), ["A", "B"]);
  // The origins move with the rows — a dropped row leaves no provenance behind.
  assert.equal(forest.origins.get("C"), undefined);
  assert.equal(forest.origins.get("B")?.revision, R2);
});

test("a collection index may never prune — absent there means deeper, not gone", () => {
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  // A depth-1 index listing only the area. root=null => no prune.
  forest = mergeSpine(forest, [node("A", null)], { subject: "plant-a", revision: R2, root: null });
  assert.deepEqual(ids(forest), ["A", "B", "C"]);
});

test("a prune touches only rows the SAME subject claims", () => {
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  // A nested publish claims C for itself.
  forest = mergeSpine(forest, [node("C", "B")], { subject: "member-c", revision: R1, root: "C" });
  // AREA re-publishes without C. C is no longer AREA's to retire.
  forest = mergeSpine(forest, [node("A", null), node("B", "A")], { subject: AREA, revision: R2, root: "A" });
  assert.deepEqual(ids(forest), ["A", "B", "C"]);
  assert.equal(forest.origins.get("C")?.subject, "member-c");
});

test("re-merging the revision already held deletes nothing", () => {
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  // A redundant fetch of the same revision is not authority to delete: it
  // says nothing the forest does not already hold.
  forest = mergeSpine(forest, [node("A", null)], { subject: AREA, revision: R1, root: "A" });
  assert.deepEqual(ids(forest), ["A", "B", "C"]);
});

test("going back to the older revision restores the pruned row", () => {
  // Storage is immutable per revision, so the R1 spine still carries C and
  // re-merging it brings C back — the prune was only ever a fact about the
  // forest in this browser.
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  forest = mergeSpine(forest, [node("A", null), node("B", "A")], { subject: AREA, revision: R2, root: "A" });
  assert.deepEqual(ids(forest), ["A", "B"]);

  forest = mergeSpine(forest, chain, { subject: AREA, revision: R1, root: "A" });
  assert.deepEqual(ids(forest), ["A", "B", "C"]);
  assert.equal(forest.origins.get("C")?.revision, R1);
});

// --- retired -------------------------------------------------------------

test("a pruned row's last parent is recorded in `retired`", () => {
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  forest = mergeSpine(forest, [node("A", null), node("B", "A")], { subject: AREA, revision: R2, root: "A" });
  assert.equal(forest.retired.get("C"), "B", "C's parent at the moment it was pruned");
});

test("a row that returns is no longer retired", () => {
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  forest = mergeSpine(forest, [node("A", null), node("B", "A")], { subject: AREA, revision: R2, root: "A" });
  assert.equal(forest.retired.has("C"), true);

  forest = mergeSpine(forest, chain, { subject: AREA, revision: R1, root: "A" });
  assert.equal(forest.retired.has("C"), false, "C is back in the forest, so it is not retired any more");
});

test("a root-level retired row records null, not a stale id", () => {
  // The root itself can be pruned too, if a later revision's document simply
  // does not carry it any more: its recorded parent is null, not a lie.
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  forest = mergeSpine(forest, [node("B", "A")], { subject: AREA, revision: R2, root: "A" });
  assert.equal(forest.retired.get("A"), null);
  assert.equal(forest.retired.get("C"), "B");
});

test("an empty document is always a no-op — never a deletion instruction", () => {
  // A subtree publish always enumerates at least its own root row; zero rows
  // is a fetch gone wrong, not a completeness claim that the subtree is now
  // empty. This must not wipe out a previously known subtree.
  let forest = mergeSpine(EMPTY_FOREST, chain, { subject: AREA, revision: R1, root: "A" });
  forest = mergeSpine(forest, [], { subject: AREA, revision: R2, root: "A" });
  assert.deepEqual(ids(forest), ["A", "B", "C"]);
});

// --- the null-parent guard, at the merge boundary -------------------------

test("a subtree top with a parent null keeps the parent an earlier slice gave", () => {
  // The collection index places area-1 under region-0. Expanding area-1 later
  // fetches its own subtree document, which — being self-contained — names
  // area-1 as ITS top with no parent. Merging that must not re-parent area-1
  // to null.
  let forest = mergeSpine(EMPTY_FOREST, [node("region-0", null), node("area-1", "region-0")], {
    subject: "plant-a",
    revision: R1,
    root: null,
  });
  assert.equal(forest.nodes.get("area-1")?.parent, "region-0");

  const subtreeDoc = [node("area-1", null), node("level-2", "area-1")];
  forest = mergeSpine(forest, subtreeDoc, { subject: "area-1", revision: R1, root: "area-1" });
  assert.equal(forest.nodes.get("area-1")?.parent, "region-0", "the known parent survives the self-declared root");
  assert.equal(forest.nodes.get("level-2")?.parent, "area-1");
});

test("an empty forest merges an empty collection index into itself as a no-op", () => {
  const forest = mergeSpine(EMPTY_FOREST, [], { subject: "plant-a", revision: R1, root: null });
  assert.equal(forest, EMPTY_FOREST, "an empty document with no root is ignored, not a deletion of nothing");
});
