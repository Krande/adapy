import assert from "node:assert/strict";
import {test} from "node:test";

import {
    computeJobOptionsFrom,
    isComputeMode,
    normaliseMemoryGb,
    normaliseWorkers,
} from "@/state/computeStore";

// The store is a persisted zustand store; what is worth pinning is the shape of
// the values it keeps and the options it hands a job, both of which are pure.

test("a worker count is a whole number of at least one, or no opinion", () => {
    assert.equal(normaliseWorkers(null), null);
    assert.equal(normaliseWorkers(undefined), null);
    assert.equal(normaliseWorkers(Number.NaN), null);
    assert.equal(normaliseWorkers(0), 1);
    assert.equal(normaliseWorkers(-3), 1);
    assert.equal(normaliseWorkers(7.6), 8);
});

test("a memory limit is a positive number of gigabytes, or no opinion", () => {
    assert.equal(normaliseMemoryGb(null), null);
    assert.equal(normaliseMemoryGb(0), null);
    assert.equal(normaliseMemoryGb(-1), null);
    assert.equal(normaliseMemoryGb(Number.POSITIVE_INFINITY), null);
    // Tenths are enough: nobody sizes a pod to the megabyte.
    assert.equal(normaliseMemoryGb(3.96), 4);
    assert.equal(normaliseMemoryGb(2.25), 2.3);
});

test("only the three named modes are modes", () => {
    assert.ok(isComputeMode("interactive"));
    assert.ok(isComputeMode("balanced"));
    assert.ok(isComputeMode("throughput"));
    assert.ok(!isComputeMode("fast"));
    assert.ok(!isComputeMode(3));
});

test("job options carry nulls for no opinion and always name the mode", () => {
    assert.deepEqual(computeJobOptionsFrom({maxWorkers: null, memoryLimitGb: null, mode: "balanced"}), {
        workers: null,
        memory_limit_gb: null,
        performance_mode: "balanced",
    });
    assert.deepEqual(computeJobOptionsFrom({maxWorkers: 4, memoryLimitGb: 4, mode: "throughput"}), {
        workers: 4,
        memory_limit_gb: 4,
        performance_mode: "throughput",
    });
    // Whatever got persisted by an older build is normalised on the way out.
    assert.deepEqual(
        computeJobOptionsFrom({maxWorkers: 0, memoryLimitGb: -2, mode: "turbo" as never}),
        {workers: 1, memory_limit_gb: null, performance_mode: "balanced"},
    );
});
