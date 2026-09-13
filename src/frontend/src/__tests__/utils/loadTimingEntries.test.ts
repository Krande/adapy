import assert from "node:assert/strict";
import {test} from "node:test";

import {addLongTasks, emptyLongTaskTotals, findResourceEntry} from "../../utils/scene/loadTimingEntries";

test("long tasks from before the load started are not counted", () => {
    const totals = emptyLongTaskTotals();
    // A buffered observer replays the whole session: two old tasks, then two
    // that belong to this load.
    addLongTasks(
        totals,
        [
            {startTime: 100, duration: 900},
            {startTime: 4_000, duration: 300},
            {startTime: 10_000, duration: 120},
            {startTime: 10_500, duration: 40},
        ],
        10_000,
    );
    assert.deepEqual(totals, {count: 2, ms: 160, blockingMs: 70});
});

test("totals accumulate across observer callbacks", () => {
    const totals = emptyLongTaskTotals();
    addLongTasks(totals, [{startTime: 10, duration: 60}], 0);
    addLongTasks(totals, [{startTime: 20, duration: 80}], 0);
    assert.deepEqual(totals, {count: 2, ms: 140, blockingMs: 40});
});

test("findResourceEntry prefers the latest observed entry for the URL", () => {
    const observed = [
        {name: "https://s.example/a.glb", id: 1},
        {name: "https://s.example/b.glb", id: 2},
        {name: "https://s.example/a.glb", id: 3},
    ];
    let bufferRead = false;
    const hit = findResourceEntry(observed, "https://s.example/a.glb", () => {
        bufferRead = true;
        return [];
    });
    assert.equal(hit?.id, 3);
    assert.equal(bufferRead, false);
});

test("findResourceEntry falls back to the global buffer", () => {
    const hit = findResourceEntry([{name: "https://s.example/other.glb", id: 1}], "https://s.example/a.glb", () => [
        {name: "https://s.example/a.glb", id: 7},
        {name: "https://s.example/a.glb", id: 8},
    ]);
    assert.equal(hit?.id, 8);
    assert.equal(findResourceEntry([], "https://s.example/a.glb", () => []), undefined);
});
