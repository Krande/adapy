// The playback clock: tracks are shown the moment the clock is at, the clock advances only when a
// frame loop ticks it, and a recording that grows moves the end.
import assert from "node:assert/strict";
import {test} from "node:test";

import {PlaybackClock} from "@/state/playbackClock";

function recorder(clock: PlaybackClock, id: string, duration: number) {
    const shown: number[] = [];
    const handle = clock.addTrack({id, duration, show: (t) => void shown.push(t)});
    return {shown, handle, last: () => shown[shown.length - 1]};
}

test("without a track the clock has no duration and does not play", () => {
    const clock = new PlaybackClock();
    assert.deepEqual(clock.state(), {duration: 0, time: 0, playing: false, speed: 1, loop: false, followEnd: false, tracks: 0});
    clock.play();
    assert.equal(clock.state().playing, false);
    assert.equal(clock.tick(1000), false, "a frame asks for no other");
});

test("a track is shown when it is added and whenever the time changes", () => {
    const clock = new PlaybackClock();
    const a = recorder(clock, "a", 10);
    assert.deepEqual(a.shown, [0], "shown at once, at the clock's time");
    assert.equal(clock.state().duration, 10);
    clock.seek(4);
    assert.equal(a.last(), 4);
    clock.seek(4);
    assert.equal(a.shown.length, 2, "the same time again shows nothing");
    clock.seek(99);
    assert.equal(a.last(), 10, "kept within the duration");
    clock.seek(-5);
    assert.equal(a.last(), 0);
});

test("the duration is the longest track's, and a shorter track is shown no further than its end", () => {
    const clock = new PlaybackClock();
    const short = recorder(clock, "short", 2);
    const long = recorder(clock, "long", 6);
    assert.equal(clock.state().duration, 6);
    clock.seek(5);
    assert.equal(long.last(), 5);
    assert.equal(short.last(), 2);
    long.handle.remove();
    assert.equal(clock.state().duration, 2);
    assert.equal(clock.time(), 2, "the time comes back inside what is left");
    assert.equal(clock.state().tracks, 1);
});

test("playing advances by the time between frames, times the speed, and stops at the end", () => {
    const clock = new PlaybackClock();
    const a = recorder(clock, "a", 1);
    let woken = 0;
    clock.onWake(() => woken++);
    clock.play();
    assert.equal(woken, 1, "a viewport that sleeps is woken");
    assert.equal(clock.tick(1000), true, "the first frame only starts the count");
    assert.equal(clock.time(), 0);
    assert.equal(clock.tick(1100), true);
    assert.ok(Math.abs(clock.time() - 0.1) < 1e-9);
    assert.ok(Math.abs(a.last() - 0.1) < 1e-9);
    clock.setSpeed(2);
    clock.tick(1200);
    assert.ok(Math.abs(clock.time() - 0.3) < 1e-9);
    // A frame after a long stall moves the time by a quarter of a second of the wall clock at most.
    clock.setSpeed(1);
    clock.tick(60_000);
    assert.ok(Math.abs(clock.time() - 0.55) < 1e-9);
    assert.equal(clock.tick(60_250), true);
    assert.equal(clock.tick(60_500), false, "the end: no further frame is asked for");
    assert.equal(clock.time(), 1);
    assert.equal(clock.state().playing, false);
    // Play from the end starts over.
    clock.play();
    assert.equal(clock.time(), 0);
    assert.equal(clock.state().playing, true);
});

test("paused, a frame changes nothing and asks for no other", () => {
    const clock = new PlaybackClock();
    const a = recorder(clock, "a", 5);
    clock.play();
    clock.tick(0);
    clock.tick(100);
    clock.pause();
    const shown = a.shown.length;
    assert.equal(clock.tick(200), false);
    assert.equal(clock.tick(300), false);
    assert.equal(a.shown.length, shown);
    // The pause is not played through when it starts again.
    clock.play();
    clock.tick(10_000);
    clock.tick(10_100);
    assert.ok(Math.abs(clock.time() - 0.2) < 1e-9);
});

test("with loop the time wraps at the end", () => {
    const clock = new PlaybackClock();
    recorder(clock, "a", 1);
    clock.setLoop(true);
    clock.seek(0.9);
    clock.play();
    clock.tick(0);
    assert.equal(clock.tick(200), true);
    assert.ok(Math.abs(clock.time() - 0.1) < 1e-9);
    assert.equal(clock.state().playing, true);
});

test("following the end, the time stays at the end as a track grows", () => {
    const clock = new PlaybackClock();
    const live = recorder(clock, "live", 3);
    clock.setFollowEnd(true);
    assert.equal(clock.time(), 3);
    assert.equal(live.last(), 3);
    live.handle.setDuration(4.5);
    assert.equal(clock.state().duration, 4.5);
    assert.equal(clock.time(), 4.5);
    assert.equal(live.last(), 4.5, "the new end is shown without a frame");
    // Held at the end it needs no frames, playing or not.
    clock.play();
    assert.equal(clock.tick(100), false);
    // A seek lets go of the end; it then plays on from there.
    clock.seek(1);
    assert.equal(clock.state().followEnd, false);
    live.handle.setDuration(6);
    assert.equal(clock.time(), 1);
    clock.tick(1000);
    assert.equal(clock.tick(1100), true);
    assert.ok(Math.abs(clock.time() - 1.1) < 1e-9);
});

test("a track that shrinks under the time brings the time back", () => {
    const clock = new PlaybackClock();
    const a = recorder(clock, "a", 10);
    clock.seek(8);
    a.handle.setDuration(5);
    assert.equal(clock.time(), 5);
    assert.equal(a.last(), 5);
});

test("the state is the same object until something changed, and listeners hear of changes", () => {
    const clock = new PlaybackClock();
    let heard = 0;
    const off = clock.subscribe(() => heard++);
    const first = clock.state();
    assert.equal(clock.state(), first);
    const a = recorder(clock, "a", 10);
    assert.ok(heard >= 1);
    assert.notEqual(clock.state(), first);
    // While it plays, the moving time is told about ten times a second, not every frame.
    clock.play();
    heard = 0;
    for (let ms = 0; ms <= 1000; ms += 10) clock.tick(ms);
    assert.ok(heard >= 8 && heard <= 12, `heard ${heard} times in a second`);
    assert.equal(a.shown.length > 90, true, "every frame is shown all the same");
    off();
    heard = 0;
    clock.pause();
    assert.equal(heard, 0);
});

test("a track that throws does not stop the others", () => {
    const clock = new PlaybackClock();
    const warn = console.warn;
    const warned: unknown[] = [];
    console.warn = (...args: unknown[]) => void warned.push(args);
    try {
        clock.addTrack({
            id: "broken",
            duration: 5,
            show: () => {
                throw new Error("no");
            },
        });
        const good = recorder(clock, "good", 5);
        clock.seek(2);
        clock.seek(3);
        assert.equal(good.last(), 3);
        assert.equal(warned.length, 1, "said once");
    } finally {
        console.warn = warn;
    }
});

test("adding a track with an id in use replaces that track", () => {
    const clock = new PlaybackClock();
    const old = recorder(clock, "a", 4);
    const next = recorder(clock, "a", 9);
    assert.equal(clock.state().tracks, 1);
    assert.equal(clock.state().duration, 9);
    // The first handle no longer reaches the clock.
    old.handle.remove();
    old.handle.setDuration(100);
    assert.equal(clock.state().tracks, 1);
    assert.equal(clock.state().duration, 9);
    clock.seek(1);
    assert.equal(next.last(), 1);
    assert.equal(old.last(), 0);
});

test("the last track leaving stops the clock and takes it back to the start", () => {
    const clock = new PlaybackClock();
    const a = recorder(clock, "a", 4);
    clock.seek(2);
    clock.play();
    a.handle.remove();
    assert.deepEqual(clock.state(), {duration: 0, time: 0, playing: false, speed: 1, loop: false, followEnd: false, tracks: 0});
});

test("a track the clock has passed the end of holds its last moment, shown once", () => {
    const clock = new PlaybackClock();
    const model = recorder(clock, "model", 1);
    const recording = recorder(clock, "recording", 5);
    clock.seek(1.5);
    clock.seek(2.5);
    clock.seek(4);
    assert.deepEqual(model.shown, [0, 1], "at its end once, and left there");
    assert.deepEqual(recording.shown, [0, 1.5, 2.5, 4]);
    clock.seek(0.5);
    assert.equal(model.last(), 0.5, "back inside it, it follows again");
});
