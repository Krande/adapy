import assert from "node:assert/strict";
import {test} from "node:test";

import {pushSnapshot, redoStep, undoStep, type HistoryStacks} from "../../utils/cellbuilder/history";

const empty = <T>(): HistoryStacks<T> => ({past: [], future: []});

test("pushSnapshot appends to past and clears future", () => {
    let h = empty<string>();
    h = pushSnapshot(h, "a", 100);
    h = pushSnapshot(h, "b", 100);
    assert.deepEqual(h.past, ["a", "b"]);
    assert.deepEqual(h.future, []);
    // a fresh change after some redo history clears the redo stack
    h = {past: ["a"], future: ["x", "y"]};
    h = pushSnapshot(h, "b", 100);
    assert.deepEqual(h, {past: ["a", "b"], future: []});
});

test("pushSnapshot caps the past at the limit (drops oldest)", () => {
    let h = empty<number>();
    for (let i = 0; i < 5; i++) h = pushSnapshot(h, i, 3);
    assert.deepEqual(h.past, [2, 3, 4]);
});

test("undoStep returns the last snapshot and moves current to future", () => {
    const h: HistoryStacks<string> = {past: ["s0", "s1"], future: []};
    const step = undoStep(h, "cur", 100);
    assert.ok(step);
    assert.equal(step!.restored, "s1");
    assert.deepEqual(step!.stacks.past, ["s0"]);
    assert.deepEqual(step!.stacks.future, ["cur"]);
});

test("undoStep on empty past returns null", () => {
    assert.equal(undoStep(empty<string>(), "cur", 100), null);
});

test("redoStep mirrors undoStep", () => {
    const h: HistoryStacks<string> = {past: ["s0"], future: ["s1", "s2"]};
    const step = redoStep(h, "cur", 100);
    assert.ok(step);
    assert.equal(step!.restored, "s1");
    assert.deepEqual(step!.stacks.past, ["s0", "cur"]);
    assert.deepEqual(step!.stacks.future, ["s2"]);
});

test("redoStep on empty future returns null", () => {
    assert.equal(redoStep({past: ["s0"], future: []}, "cur", 100), null);
});

test("undo then redo round-trips the current state", () => {
    // edit A -> edit B history, then undo/redo returns to B
    let h: HistoryStacks<string> = {past: ["A"], future: []};
    // current is "B"
    const u = undoStep(h, "B", 100)!;
    assert.equal(u.restored, "A"); // now showing A
    h = u.stacks;
    const r = redoStep(h, "A", 100)!; // current is now "A" (restored)
    assert.equal(r.restored, "B"); // back to B
    assert.deepEqual(r.stacks, {past: ["A"], future: []});
});
