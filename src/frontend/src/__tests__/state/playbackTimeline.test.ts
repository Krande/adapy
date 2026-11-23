// The timeline's arithmetic (state/playbackTimeline.ts): steps and sample snapping, the fine-scrub
// speed, the zoomed window, the precise readout, typed times and the capture's file name.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  captureFilename,
  clampWindow,
  followTime,
  isZoomed,
  mergeSamples,
  MIN_WINDOW_SECONDS,
  nearestSample,
  parseTimeInput,
  preciseClockText,
  resolveStep,
  scrubScale,
  scrubTime,
  stepSamples,
  stepSizeLabel,
  stepTarget,
  STEP_SIZES,
  timeToFraction,
  wheelZoomFactor,
  windowFromSelection,
  xToTime,
  zoomAt,
} from "@/state/playbackTimeline";

const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 1e-9, `${a} ≈ ${b}${msg ? `: ${msg}` : ""}`);

test("the offered step sizes, 1 frame first, and their labels", () => {
  assert.deepEqual(STEP_SIZES, ["frame", 0.01, 0.1, 1, 10]);
  assert.deepEqual(STEP_SIZES.map(stepSizeLabel), ["1 frame", "10 ms", "100 ms", "1 s", "10 s"]);
});

test("Shift steps ten times further, Alt a tenth; a frame step ×10 is ten samples, ÷10 a tenth of a frame", () => {
  assert.deepEqual(resolveStep(0.1), { kind: "seconds", seconds: 0.1 });
  near((resolveStep(0.1, { shift: true }) as { seconds: number }).seconds, 1);
  near((resolveStep(0.1, { alt: true }) as { seconds: number }).seconds, 0.01);
  near((resolveStep(1, { shift: true, alt: true }) as { seconds: number }).seconds, 1);
  assert.deepEqual(resolveStep("frame"), { kind: "samples", count: 1 });
  assert.deepEqual(resolveStep("frame", { shift: true }), { kind: "samples", count: 10 });
  const fine = resolveStep("frame", { alt: true }, 1 / 50);
  assert.equal(fine.kind, "seconds");
  near((fine as { seconds: number }).seconds, 1 / 500);
});

test("a sample step goes to the next sample, from on one or between two, and stops at the ends", () => {
  const times = [0, 0.1, 0.2, 0.25, 1];
  assert.equal(stepSamples(times, 0, 1), 0.1);
  assert.equal(stepSamples(times, 0.1, 1), 0.2, "on a sample: the one after it, not itself");
  assert.equal(stepSamples(times, 0.15, 1), 0.2, "between two: the next, not past it");
  assert.equal(stepSamples(times, 0.15, -1), 0.1);
  assert.equal(stepSamples(times, 0.2, -1), 0.1);
  assert.equal(stepSamples(times, 0, 1, 3), 0.25, "three samples on");
  assert.equal(stepSamples(times, 1, 1), null);
  assert.equal(stepSamples(times, 0, -1), null);
  assert.equal(stepSamples([], 0, 1), null);
  // A time a hair off a sample (float noise from a seek) counts as on it.
  assert.equal(stepSamples(times, 0.2 - 1e-9, 1), 0.25);
  assert.equal(nearestSample(times, 0.23), 0.25);
  assert.equal(nearestSample(times, 0.6), 0.25);
  assert.equal(nearestSample(times, 5), 1);
  assert.equal(nearestSample([], 5), null);
});

test("several tracks' samples merge into one sorted list without repeats", () => {
  assert.deepEqual([...mergeSamples([[0, 0.5, 1], [0.5, 0.25, NaN, 1 + 1e-9]])], [0, 0.25, 0.5, 1]);
});

test("a step lands within the timeline; without samples a frame step is one display frame", () => {
  const samples = [0, 0.004, 0.5];
  const next = (t: number, d: 1 | -1) => stepSamples(samples, t, d);
  near(stepTarget(0, 1, resolveStep("frame"), 2, next, true), 0.004, "the quick moment is not stepped over");
  near(stepTarget(0.5, 1, resolveStep("frame"), 2, next, true), 2, "past the last sample: the end");
  near(stepTarget(0.004, -1, resolveStep("frame", { shift: true }), 2, next, true), 0, "ten back from the second: the start");
  near(stepTarget(1, 1, resolveStep("frame"), 2, null, false, 1 / 30), 1 + 1 / 30);
  near(stepTarget(1.95, 1, resolveStep(0.1), 2, null, false), 2);
  near(stepTarget(0.05, -1, resolveStep(0.1), 2, null, false), 0);
  near(stepTarget(1, -1, resolveStep(0.01), 2, null, false), 0.99);
});

test("a drag is 1/10 with Shift or pulled off the bar, 1/100 with both or pulled further; a long press is fine", () => {
  assert.equal(scrubScale({}), 1);
  assert.equal(scrubScale({ shift: true }), 0.1);
  assert.equal(scrubScale({ pullPx: 50 }), 0.1);
  assert.equal(scrubScale({ pullPx: -50 }), 0.1, "pulled up or down");
  assert.equal(scrubScale({ pullPx: 150 }), 0.01);
  assert.equal(scrubScale({ pullPx: 50, shift: true }), 0.01);
  assert.equal(scrubScale({ fine: true }), 0.1);
  assert.equal(scrubScale({ fine: true, shift: true }), 0.01);
  // A bar 1000 px wide over 10 s: a pixel is 10 ms; at 1/10, 1 ms; at 1/100, 0.1 ms.
  const pxPerSecond = 100;
  near(scrubTime(5, 1, pxPerSecond, 1, 0, 10), 5.01);
  near(scrubTime(5, 1, pxPerSecond, 0.1, 0, 10), 5.001);
  near(scrubTime(5, 1, pxPerSecond, 0.01, 0, 10), 5.0001);
  near(scrubTime(9.99, 100, pxPerSecond, 1, 0, 10), 10, "kept inside");
  near(scrubTime(2, -5, pxPerSecond, 1, 2, 3), 2, "kept inside a zoomed window");
});

test("the zoomed window maps the bar at full resolution, zooms about the pointer and pages with the time", () => {
  const all = { start: 0, end: 100 };
  near(xToTime(250, 1000, all), 25);
  const view = { start: 10, end: 11 };
  near(xToTime(500, 1000, view), 10.5, "the whole bar spans the zoomed second");
  near(xToTime(-5, 1000, view), 10, "clamped to the bar");
  near(timeToFraction(10.25, view), 0.25);
  // Wheel up zooms in about the time under the pointer, which stays there.
  const zoomed = zoomAt(all, 25, 4, 100);
  near(zoomed.end - zoomed.start, 25);
  near(timeToFraction(25, zoomed), 0.25);
  assert.ok(wheelZoomFactor(-100) > 1 && wheelZoomFactor(100) < 1);
  // Never past the ends, never narrower than the minimum.
  const out = zoomAt(all, 0, 0.1, 100);
  assert.deepEqual(out, { start: 0, end: 100 });
  const tiny = zoomAt(view, 10.5, 1e6, 100);
  near(tiny.end - tiny.start, MIN_WINDOW_SECONDS);
  assert.deepEqual(clampWindow({ start: 95, end: 110 }, 100), { start: 85, end: 100 });
  assert.equal(isZoomed(all, 100), false);
  assert.equal(isZoomed(view, 100), true);
  // A drag-selected range, either way round; a click is no range.
  assert.deepEqual(windowFromSelection(30, 20, 100), { start: 20, end: 30 });
  assert.equal(windowFromSelection(20, 20.001, 100), null);
  // The time leaves the window: it pages on.
  assert.equal(followTime(view, 10.5, 100), view);
  assert.deepEqual(followTime(view, 11.2, 100), { start: 11.2, end: 12.2 });
});

test("the time reads mm:ss.mmm", () => {
  assert.equal(preciseClockText(0), "00:00.000");
  assert.equal(preciseClockText(1.2345), "00:01.235");
  assert.equal(preciseClockText(83.456), "01:23.456");
  assert.equal(preciseClockText(59.9996), "01:00.000", "rounding carries");
  assert.equal(preciseClockText(6000), "100:00.000");
  assert.equal(preciseClockText(-1), "00:00.000");
});

test("a typed time: m:ss.mmm, h:mm:ss, seconds, ms, and +/- from now", () => {
  near(parseTimeInput("1:23.456")!, 83.456);
  near(parseTimeInput("01:23.456")!, 83.456);
  near(parseTimeInput("1:02:03.5")!, 3723.5);
  assert.equal(parseTimeInput("83.456"), 83.456);
  assert.equal(parseTimeInput(" 83.456 s "), 83.456);
  assert.equal(parseTimeInput("1500ms"), 1.5);
  assert.equal(parseTimeInput(".5"), 0.5);
  assert.equal(parseTimeInput("2,5"), 2.5, "a comma is a decimal point");
  near(parseTimeInput("+0.1", 2)!, 2.1);
  near(parseTimeInput("-250ms", 2)!, 1.75);
  for (const bad of ["", "abc", "1:75", "1:2:3:4", "1.2.3", "--1", "1:60.0"]) assert.equal(parseTimeInput(bad), null, bad);
});

test("a captured frame is named by its time to the millisecond, safe on every file system", () => {
  assert.equal(captureFilename(83.456), "frame_t01m23.456s.png");
  assert.equal(captureFilename(0.0004), "frame_t00m00.000s.png");
  assert.equal(captureFilename(1.5, "weld pass/2"), "weld-pass-2_t00m01.500s.png");
  assert.equal(captureFilename(1.5, "::"), "frame_t00m01.500s.png");
  assert.doesNotMatch(captureFilename(3599.999), /[:\\/*?"<>|]/);
});
