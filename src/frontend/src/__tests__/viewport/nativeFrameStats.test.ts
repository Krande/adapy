// The stats panel's frame timing on the three.js-free viewport: an idle spell between frames is not a
// slow frame, and the rate is that of the frames drawn in the last second.
import {test} from "node:test";
import assert from "node:assert/strict";
import {createFrameRing, intervalsInOrder, recordFrame, summarize} from "../../viewport/native/frameStats";

test("a run of frames at 60 per second reads as 60 fps with 16.7 ms frames", () => {
    const ring = createFrameRing();
    for (let i = 0; i < 61; i++) recordFrame(ring, 1000 + i * (1000 / 60), 2, 4);
    const s = summarize(ring, 2000);
    assert.equal(s.idle, false);
    assert.ok(Math.abs(s.fps! - 60) < 0.5, `fps ${s.fps}`);
    assert.ok(Math.abs(s.p50! - 1000 / 60) < 0.01);
    assert.equal(s.cpuMs, 2);
    assert.equal(s.gpuMs, 4);
});

test("the gap between two runs is not counted as a frame", () => {
    const ring = createFrameRing();
    for (let i = 0; i < 10; i++) recordFrame(ring, i * 16, 1, 0);
    for (let i = 0; i < 10; i++) recordFrame(ring, 5000 + i * 16, 1, 0);
    const s = summarize(ring, 5150);
    assert.equal(s.max, 16);
    const intervals = intervalsInOrder(ring);
    assert.equal(intervals.length, 20);
    assert.ok(Number.isNaN(intervals[0]) && Number.isNaN(intervals[10]), "each run starts without an interval");
});

test("no frame for a while is idle, with no rate", () => {
    const ring = createFrameRing();
    for (let i = 0; i < 10; i++) recordFrame(ring, i * 16, 1, 0);
    const s = summarize(ring, 2000);
    assert.equal(s.idle, true);
    assert.equal(s.fps, null);
    assert.equal(s.p50, 16, "the frame times kept still describe the last run");
});

test("the same frame seen twice is recorded once", () => {
    const ring = createFrameRing();
    recordFrame(ring, 100, 1, 0);
    recordFrame(ring, 100, 1, 0);
    assert.equal(ring.count, 1);
});

test("the ring keeps the newest frames", () => {
    const ring = createFrameRing(4);
    for (let i = 0; i < 6; i++) recordFrame(ring, i * 10, i, 0);
    assert.equal(ring.count, 4);
    assert.equal(summarize(ring, 60).cpuMs, (2 + 3 + 4 + 5) / 4);
});
