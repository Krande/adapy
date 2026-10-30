import assert from "node:assert/strict";
import test from "node:test";

import {sweepCountsText} from "@/components/admin/applyChangesItem";
import {budgetUsage, defaultBudgetLabel, parseMaxParallel} from "@/components/admin/runBudget";

test("the empty field means all live workers, named", () => {
    assert.equal(defaultBudgetLabel({live_workers: 4, declared: 5}), "all live workers (4)");
    assert.equal(defaultBudgetLabel({live_workers: 9, declared: 5}), "all live workers (5)");
    assert.equal(defaultBudgetLabel({live_workers: 3, declared: 1}), "one at a time (the provider declares no parallel work)");
    assert.equal(defaultBudgetLabel({live_workers: null, declared: 5}), "all live workers");
    assert.equal(defaultBudgetLabel(null), "all live workers");
});

test("max parallel jobs: blank is the default, a whole number >= 1 is a cap, anything else is refused", () => {
    assert.deepEqual(parseMaxParallel("  "), {value: null});
    assert.deepEqual(parseMaxParallel("3"), {value: 3});
    for (const bad of ["0", "-1", "2.5", "two"]) assert.ok("error" in parseMaxParallel(bad), bad);
});

test("slots in use, and items waiting for or busy in another run", () => {
    assert.equal(budgetUsage({provider: "p", cap: 5, live_workers: 5, declared: 5, max_parallel: null, in_use: 2}), "2 of 5 in use now");
    assert.equal(budgetUsage(null), "");
    assert.equal(
        sweepCountsText({total: 4, counts: {done: 1, waiting: 2, busy: 1}}),
        "2/4 · 2 waiting for another run · 1 busy in another run",
    );
});
