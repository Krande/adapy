import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatStepTime,
  initialStepIndex,
  isTimeHistory,
  nextTimeHistoryStep,
  timeDecimals,
  type TimeHistoryClock,
} from "../../utils/scene/fea/timeHistory";

test("a transient field opens on its last frame, everything else on step 0", () => {
  assert.equal(initialStepIndex({ analysis_kind: "transient", n_steps: 41 }), 40);
  assert.equal(initialStepIndex({ analysis_kind: "static", n_steps: 41 }), 0);
  assert.equal(initialStepIndex({ analysis_kind: "eigen", n_steps: 10 }), 0);
  assert.equal(initialStepIndex(null), 0);
  assert.equal(isTimeHistory({ analysis_kind: "transient" }), true);
  assert.equal(isTimeHistory({ analysis_kind: "static" }), false);
});

test("play advances one frame per 1/fps seconds and loops", () => {
  const clock: TimeHistoryClock = { elapsed: 0, inFlight: false };
  assert.equal(nextTimeHistoryStep(clock, 0.05, 0, 3, 10), null); // not time yet
  assert.equal(nextTimeHistoryStep(clock, 0.06, 0, 3, 10), 1);
  assert.equal(nextTimeHistoryStep(clock, 0.1, 2, 3, 10), 0); // wraps to the first frame
});

test("play waits while a frame is still loading", () => {
  const clock: TimeHistoryClock = { elapsed: 0, inFlight: true };
  assert.equal(nextTimeHistoryStep(clock, 1.0, 0, 3, 10), null);
  clock.inFlight = false;
  assert.equal(nextTimeHistoryStep(clock, 0.0, 0, 3, 10), 1);
});

test("a single-frame field never advances", () => {
  const clock: TimeHistoryClock = { elapsed: 0, inFlight: false };
  assert.equal(nextTimeHistoryStep(clock, 10, 0, 1), null);
});


test("frame times get one decimal count per history, enough to tell frames apart", () => {
  const steps = [0, 0.0015010156, 0.003001, 0.0600000657];
  assert.equal(timeDecimals(steps), 3);
  assert.equal(formatStepTime(0.0600000657, steps), "0.060");
  assert.equal(formatStepTime(0, steps), "0.000"); // same width as the rest
  assert.equal(timeDecimals([0, 1, 2]), 0);
  assert.equal(timeDecimals([0.5]), 3); // single frame: a sensible default
});
