// The two orphan causes, and the path that answers "where was it?".
//
// Ported from orphans.test.ts. This module's API is now generic:
// `OrphanEntry.id` instead of `ref`, and `lastKnownPath<T>` takes a
// `labelOf: (data: T) => string` rather than assuming a fixed node shape.

import assert from "node:assert/strict";
import { test } from "node:test";

import { buildHierarchy } from "../../assets/hierarchy";
import { lastKnownPath, orphanCause, orphanHeading, orphanSentence, type OrphanEntry } from "../../assets/orphans";

const R1 = "20260825T000000Z";
const R2 = "20260827T000000Z";
const label = (r: string) => (r === R1 ? "25 Aug" : "27 Aug");

interface TestNode {
  readonly kind: string;
  readonly name: string;
}

const node = (name: string): TestNode => ({ kind: "level", name });

// ---------------------------------------------------------------------------
// which of the two things happened
// ---------------------------------------------------------------------------

test("an entry OLDER than the hierarchy was removed by a newer sweep", () => {
  assert.equal(orphanCause(R1, R2), "removed");
});

test("an entry NEWER than the hierarchy outran the sweep", () => {
  assert.equal(orphanCause(R2, R1), "ahead");
});

test("equal revisions cannot be 'the tree moved' — that reads as ahead", () => {
  assert.equal(orphanCause(R1, R1), "ahead");
});

test("the two headings are distinct — a shared string for both would be a bug in the panel", () => {
  assert.notEqual(orphanHeading("removed"), orphanHeading("ahead"));
});

test("both revisions are IN the sentence, not in a tooltip", () => {
  // A `title` is unreachable on touch, and the list is short.
  const removed: OrphanEntry = { id: "n-0042", revision: R1, spineRevision: R2, cause: "removed", path: [] };
  const s = orphanSentence(removed, label);
  assert.match(s, /25 Aug/);
  assert.match(s, /27 Aug/);
  assert.match(s, /still there/);

  const ahead: OrphanEntry = { ...removed, revision: R2, spineRevision: R1, cause: "ahead" };
  const a = orphanSentence(ahead, label);
  assert.match(a, /25 Aug/);
  assert.match(a, /a newer hierarchy publish would show where it sits/);
});

test("the sentences do not claim the opposite of each other's fact", () => {
  const base: OrphanEntry = { id: "n-0042", revision: R1, spineRevision: R2, cause: "removed", path: [] };
  assert.match(orphanSentence(base, label), /no longer contains/);
  assert.doesNotMatch(
    orphanSentence({ ...base, cause: "ahead", revision: R2, spineRevision: R1 }, label),
    /no longer contains/,
  );
});

// ---------------------------------------------------------------------------
// the path — what a list can answer without a tombstone in the tree
// ---------------------------------------------------------------------------

const FOREST = buildHierarchy(
  [
    { id: "area-1", parent: null, data: node("Area 1") },
    { id: "level-2", parent: "area-1", data: node("Level 2") },
  ],
);

test("an orphan whose ancestors are loaded reports where it sat, outermost first", () => {
  // n-0009 is NOT in the forest; its declared parent is, and so is that
  // parent's.
  const path = lastKnownPath(
    FOREST,
    "n-0009",
    (id) => (id === "n-0009" ? "level-2" : (FOREST.byId.get(id)?.parent ?? null)),
    (data) => data.name,
  );
  assert.deepEqual(path, ["Area 1", "Level 2"]);
});

test("an orphan no spine can place reports an empty path rather than a guess", () => {
  assert.deepEqual(lastKnownPath(FOREST, "n-9999", () => null, (data) => data.name), []);
});

test("a cycle in published data terminates", () => {
  const parents: Record<string, string> = { "n-a": "n-b", "n-b": "n-a" };
  const path = lastKnownPath(FOREST, "n-a", (id) => parents[id] ?? null, (data) => data.name);
  assert.ok(path.length < 32);
});
