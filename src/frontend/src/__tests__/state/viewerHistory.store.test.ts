import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  describeViewerAction,
  diffSets,
  sameSelection,
  useViewerHistoryStore,
  VIEWER_HISTORY_LIMIT,
  type ViewerHistoryEntry,
} from "@/state/viewerHistoryStore";

// The undo / redo history of viewer operations (Ctrl+Z / Ctrl+Y). Entries are closure pairs; here
// they move a counter so each step is observable.

const st = () => useViewerHistoryStore.getState();

let value = 0;
function step(label: string, by = 1): ViewerHistoryEntry {
  value += by;
  return { label, undo: () => { value -= by; }, redo: () => { value += by; } };
}

beforeEach(() => {
  st().reset();
  value = 0;
});

test("record, undo and redo walk the history both ways", () => {
  st().record(step("a"));
  st().record(step("b", 10));
  assert.equal(value, 11);

  assert.equal(st().undo()?.label, "b");
  assert.equal(value, 1);
  assert.equal(st().undo()?.label, "a");
  assert.equal(value, 0);
  assert.equal(st().undo(), null, "nothing left to undo");

  assert.equal(st().redo()?.label, "a");
  assert.equal(st().redo()?.label, "b");
  assert.equal(value, 11);
  assert.equal(st().redo(), null, "nothing left to redo");
  assert.deepEqual(st().lastApplied && { d: st().lastApplied!.direction, l: st().lastApplied!.label }, { d: "redo", l: "b" });
});

test("a new action clears what could be redone", () => {
  st().record(step("a"));
  st().record(step("b"));
  st().undo();
  assert.equal(st().future.length, 1);
  st().record(step("c"));
  assert.equal(st().future.length, 0);
  assert.equal(st().redo(), null);
  assert.deepEqual(st().past.map((e) => e.label), ["a", "c"]);
});

test("the history is bounded: the oldest entry falls off", () => {
  for (let i = 0; i < VIEWER_HISTORY_LIMIT + 5; i++) st().record(step(`s${i}`));
  assert.equal(st().past.length, VIEWER_HISTORY_LIMIT);
  assert.equal(st().past[0].label, "s5");
  assert.equal(st().past[st().past.length - 1].label, `s${VIEWER_HISTORY_LIMIT + 4}`);
});

test("nothing records while an entry is being undone or redone", () => {
  const entry: ViewerHistoryEntry = {
    label: "outer",
    // An action triggered by the undo itself would try to record -- it must not.
    undo: () => st().record(step("inner")),
    redo: () => st().record(step("inner")),
  };
  st().record(entry);
  st().undo();
  assert.equal(st().applying, false);
  assert.deepEqual(st().past.map((e) => e.label), []);
  assert.deepEqual(st().future.map((e) => e.label), ["outer"]);
  st().redo();
  assert.deepEqual(st().past.map((e) => e.label), ["outer"]);
  assert.equal(st().future.length, 0);
});

test("reset forgets both stacks", () => {
  st().record(step("a"));
  st().record(step("b"));
  st().undo();
  st().reset();
  assert.equal(st().past.length, 0);
  assert.equal(st().future.length, 0);
  assert.equal(st().lastApplied, null);
  assert.equal(st().undo(), null);
  assert.equal(st().redo(), null);
});

test("diffSets reports what was added and removed", () => {
  const d = diffSets(new Set(["a", "b"]), new Set(["b", "c", "d"]));
  assert.deepEqual(d.added.sort(), ["c", "d"]);
  assert.deepEqual(d.removed, ["a"]);
});

test("sameSelection compares contents, not map identity", () => {
  const k1 = {};
  const k2 = {};
  assert.ok(sameSelection(new Map(), new Map()));
  assert.ok(sameSelection(new Map([[k1, new Set(["a", "b"])]]), new Map([[k1, new Set(["b", "a"])]])));
  assert.ok(sameSelection(new Map([[k1, new Set<string>()]]), new Map()), "an empty entry selects nothing");
  assert.ok(!sameSelection(new Map([[k1, new Set(["a"])]]), new Map([[k2, new Set(["a"])]])));
  assert.ok(!sameSelection(new Map([[k1, new Set(["a"])]]), new Map([[k1, new Set(["a", "b"])]])));
});

test("labels name what changed", () => {
  assert.equal(describeViewerAction("hide", { hidden: 12, shown: 0, selected: 0 }), "hide 12 objects");
  assert.equal(describeViewerAction("show", { hidden: 0, shown: 1, selected: 0 }), "show 1 object");
  assert.equal(describeViewerAction("select", { hidden: 0, shown: 0, selected: 500 }), "select 500 objects");
  assert.equal(describeViewerAction("select", { hidden: 0, shown: 0, selected: 0 }), "clear selection");
});
