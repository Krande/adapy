import {test} from "node:test";
import assert from "node:assert/strict";

import {ShardScheduler, shardWorkerCount, type ShardTask} from "../../utils/nativeConvert/shardSchedule";

// Drive a schedule with `workers` simulated workers taking turns; returns the tasks in the order run.
function drain(s: ShardScheduler, workers: number): ShardTask[] {
    const ran: ShardTask[] = [];
    const inFlight: (ShardTask | null)[] = Array(workers).fill(null);
    for (let guard = 0; guard < 10000; guard++) {
        let progressed = false;
        for (let w = 0; w < workers; w++) {
            if (inFlight[w]) {
                // finish the task this worker held, then ask again
                s.complete(inFlight[w]!);
                inFlight[w] = null;
                progressed = true;
            }
            const t = s.next();
            if (t === "done") continue;
            if (t === "wait") continue;
            inFlight[w] = t;
            ran.push(t);
            progressed = true;
        }
        if (!progressed && s.next() === "done") return ran;
    }
    throw new Error("schedule did not finish");
}

test("every batch runs once, in order, and the schedule ends", () => {
    const s = new ShardScheduler(3, [], [4, 9, 10]);
    const ran = drain(s, 3);
    assert.deepEqual(
        ran.map((t) => (t.kind === "batch" ? [t.begin, t.end] : null)),
        [
            [0, 4],
            [4, 9],
            [9, 10],
        ],
    );
    assert.equal(s.total, 3);
    assert.equal(s.next(), "done");
});

test("a huge root's ranges cover its faces once, and its assembly follows all of them", () => {
    const s = new ShardScheduler(2, [1000], [5]);
    const ran = drain(s, 2);
    const ranges = ran.filter((t) => t.kind === "huge") as Extract<ShardTask, {kind: "huge"}>[];
    // contiguous, non-overlapping, covering [0, 1000)
    let at = 0;
    for (const r of ranges) {
        assert.equal(r.f0, at);
        at = r.f1;
    }
    assert.equal(at, 1000);
    const asm = ran.findIndex((t) => t.kind === "assemble");
    const lastRange = ran.map((t) => t.kind).lastIndexOf("huge");
    assert.ok(asm > lastRange, "assembly only after every range");
    assert.equal(ran.filter((t) => t.kind === "assemble").length, 1);
    const a = ran[asm] as Extract<ShardTask, {kind: "assemble"}>;
    assert.equal(a.chunk, ranges[0].f1 - ranges[0].f0);
    assert.equal(s.total, ranges.length + 1 + 1);
});

test("huge ranges go out before ordinary batches; ranges never smaller than 64 faces", () => {
    const s = new ShardScheduler(8, [200], [3, 6]);
    const first = s.next() as ShardTask;
    assert.equal(first.kind, "huge");
    s.complete(first);
    const ran = [first, ...drain(s, 8)];
    const ranges = ran.filter((t) => t.kind === "huge") as Extract<ShardTask, {kind: "huge"}>[];
    assert.ok(ranges.every((r, i) => i === ranges.length - 1 || r.f1 - r.f0 >= 64));
});

test("a worker waits while an assembly is pending, instead of finishing early", () => {
    const s = new ShardScheduler(2, [128], []);
    const a = s.next() as ShardTask; // range 0..64
    const b = s.next() as ShardTask; // range 64..128
    assert.equal(a.kind, "huge");
    assert.equal(b.kind, "huge");
    assert.equal(s.next(), "wait"); // nothing to take, but the assembly is not out yet
    s.complete(a);
    assert.equal(s.next(), "wait");
    s.complete(b);
    const asm = s.next() as ShardTask;
    assert.equal(asm.kind, "assemble");
    assert.equal(s.next(), "wait"); // the assembly is still running
    s.complete(asm);
    assert.equal(s.next(), "done");
});

test("worker count: one core is kept for the page, memory bounds big sources", () => {
    const MB = 1024 * 1024;
    assert.equal(shardWorkerCount(50 * MB, {cores: 16, deviceMemoryGb: 8}), 8); // capped
    assert.equal(shardWorkerCount(50 * MB, {cores: 4, deviceMemoryGb: 8}), 3);
    assert.equal(shardWorkerCount(50 * MB, {cores: 1, deviceMemoryGb: 8}), 1);
    // a 1 GB source on an 8 GB device: ~768 MB per worker against a 4 GB budget
    assert.equal(shardWorkerCount(1024 * MB, {cores: 16, deviceMemoryGb: 8}), 5);
    // a small device does not shard a big source
    assert.equal(shardWorkerCount(1024 * MB, {cores: 8, deviceMemoryGb: 1}), 1);
    // unknown device: 4 cores, 4 GB assumed
    assert.equal(shardWorkerCount(100 * MB), 3);
});
