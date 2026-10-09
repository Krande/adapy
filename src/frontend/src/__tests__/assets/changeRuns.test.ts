import assert from "node:assert/strict";
import { test } from "node:test";

import {
  changedCount,
  filterRunItems,
  itemsByUser,
  runLabel,
  runMarks,
  treeAction,
  type RunItem,
  type RunSummary,
} from "@/assets/changeRuns";

// A picked change-check run is what the Sources tab draws and lists. These pin the rules that
// would answer wrongly if they drifted: an unpublished item marking a node, a weaker action
// hiding a deletion, a filter missing the row someone searched for.

function item(over: Partial<RunItem> = {}): RunItem {
  return {
    node: "n1",
    action: "modified",
    changed_at: "2026-10-08T14:03:00Z",
    changed_by: "jdoe",
    name: "/SITE-A/ZONE-1",
    detail: null,
    ...over,
  };
}

function run(over: Partial<RunSummary> = {}): RunSummary {
  return {
    status: "done",
    created_at: "2026-10-09T02:00:00Z",
    requested_via: "schedule",
    stale: true,
    up_to_date: false,
    counts: { added: 1, modified: 2, deleted: 0 },
    users: ["jdoe", "asmith"],
    message: null,
    error: null,
    ...over,
  };
}

test("provider actions fold onto the tree's three marks", () => {
  assert.equal(treeAction("added"), "added");
  assert.equal(treeAction("deleted"), "deleted");
  for (const a of ["modified", "changed", "unknown", "anything"]) assert.equal(treeAction(a), "modified");
});

test("run marks skip unpublished items and keep the strongest action per node", () => {
  const marks = runMarks([
    item({ node: "n1", action: "changed" }),
    item({ node: "n1", action: "deleted" }),
    item({ node: "n1", action: "added" }),
    item({ node: "n2", action: "added" }),
    item({ node: null, action: "added" }),
  ]);
  assert.deepEqual([...marks], [["n1", "deleted"], ["n2", "added"]]);
});

test("rows filter by user, tree action and a search over name, id and detail", () => {
  const items = [
    item({ node: "a", changed_by: "jdoe", action: "changed", name: "/SITE-A/FRMW-1" }),
    item({ node: "b", changed_by: "asmith", action: "added", detail: "new beam" }),
    item({ node: "c", changed_by: null, action: "deleted", name: null }),
  ];
  assert.deepEqual(filterRunItems(items, { user: "jdoe" }).map((i) => i.node), ["a"]);
  assert.deepEqual(filterRunItems(items, { action: "modified" }).map((i) => i.node), ["a"]);
  assert.deepEqual(filterRunItems(items, { query: "BEAM" }).map((i) => i.node), ["b"]);
  assert.deepEqual(filterRunItems(items, { query: "c" }).map((i) => i.node), ["c"]);
  assert.equal(filterRunItems(items, {}).length, 3);
});

test("users are counted most first", () => {
  const items = [item({ changed_by: "b" }), item({ changed_by: "a" }), item({ changed_by: "b" }), item({ changed_by: null })];
  assert.deepEqual(itemsByUser(items), [["b", 2], ["a", 1]]);
});

test("the picker's line says when, what and how", () => {
  const t = () => "09 Oct 02:00";
  assert.equal(runLabel(run(), t), "09 Oct 02:00 · 3 changed by 2 users · scheduled");
  assert.equal(runLabel(run({ users: ["x"], requested_via: "user" }), t), "09 Oct 02:00 · 3 changed by 1 user · manual");
  assert.equal(runLabel(run({ up_to_date: true, stale: false, counts: null }), t), "09 Oct 02:00 · up to date · scheduled");
  assert.equal(runLabel(run({ status: "queued" }), t), "09 Oct 02:00 · running… · scheduled");
  assert.equal(runLabel(run({ status: "error" }), t), "09 Oct 02:00 · failed · scheduled");
  assert.equal(runLabel(run({ counts: null }), t), "09 Oct 02:00 · changed · scheduled");
  assert.equal(runLabel(run({ counts: null, stale: false }), t), "09 Oct 02:00 · could not tell · scheduled");
});

test("changed count sums what the provider reported", () => {
  assert.equal(changedCount(run()), 3);
  assert.equal(changedCount(run({ counts: null })), null);
});
