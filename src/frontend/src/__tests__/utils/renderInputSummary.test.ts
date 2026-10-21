import {strict as assert} from "node:assert";
import {test} from "node:test";

import {summariseInput, summariseLongTasks} from "../../utils/scene/renderInputSummary";

const win = {
    connection: "connected",
    device: "3D mouse",
    product_id: "c62f",
    hidReports: 6,
    wheels: 0,
    gaps: [8, 8, 9, 120, 300],
    slowGaps: [
        [1000, 1120],
        [2000, 2300],
    ] as Array<[number, number]>,
    gestures: 2,
    staleCuts: 1,
};

test("summariseLongTasks counts and times the window's tasks", () => {
    assert.deepEqual(summariseLongTasks([[0, 60], [100, 190]]), {long_tasks: 2, long_task_ms: 150, long_task_max_ms: 90});
    assert.deepEqual(summariseLongTasks([]), {long_tasks: 0, long_task_ms: 0, long_task_max_ms: 0});
});

test("summariseInput tells a gap a long task covered from one the device left empty", () => {
    // The first slow gap sits inside a long task, the second has none near it.
    const s = summariseInput(win, [[990, 1110]]);
    assert.equal(s.reports, 5);
    assert.equal(s.gap_ms_p50, 9);
    assert.equal(s.gap_ms_max, 300);
    assert.equal(s.slow_gaps, 2);
    assert.equal(s.slow_gaps_blocked, 1);
    assert.equal(s.slow_gap_ms, 420);
    assert.equal(s.slow_gap_ms_in_long_tasks, 110);
    assert.equal(s.stale_cuts, 1);
});

test("summariseInput never counts more covered time than the gap itself", () => {
    const s = summariseInput(win, [[900, 1200], [1000, 1120]]);
    assert.equal(s.slow_gap_ms_in_long_tasks, 120);
    assert.equal(s.slow_gaps_blocked, 1);
});
