import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { shouldShowCapacityPanel } from "../../components/capacity/capacityFormat";
import CapacityRunStatus, {
  formatRunElapsed,
  runRow,
  stepRow,
} from "../../components/capacity/CapacityRunStatus";
import type {
  CapacityCalcProgress,
  CapacityCalcRunProgress,
  CapacityCalcStep,
} from "../../state/capacityResultsStore";

const run = (over: Partial<CapacityCalcRunProgress> = {}): CapacityCalcRunProgress => ({
  id: "run-001",
  scope: "stiffened_panel",
  label: "DNV-RP-C201 stiffened panel",
  cases_total: 84,
  cases_ready: [],
  complete: false,
  ...over,
});

const step = (over: Partial<CapacityCalcStep> = {}): CapacityCalcStep => ({
  label: "Recovering reusable stress fields for basic cases",
  done: false,
  elapsed_s: 0,
  ...over,
});

const progress = (over: Partial<CapacityCalcProgress> = {}): CapacityCalcProgress => ({
  complete: false,
  phase: "checking",
  message: "Running DNV-RP-C201 stiffened-panel checks",
  cases_total: 84,
  cases_done: 32,
  cases_known: true,
  elapsed_s: 134,
  runs: [run({ started: true, cases_ready: ["9"] })],
  ...over,
});

describe("formatRunElapsed", () => {
  it("keeps short runs in plain seconds", () => {
    assert.equal(formatRunElapsed(42), "42s");
  });

  it("pads seconds so the counter does not jitter as it ticks", () => {
    assert.equal(formatRunElapsed(242), "4m 02s");
    assert.equal(formatRunElapsed(600), "10m 00s");
  });

  it("switches to hours on a full-model run", () => {
    assert.equal(formatRunElapsed(3840), "1h 04m");
  });

  it("reads zero for a run that has not started timing", () => {
    assert.equal(formatRunElapsed(undefined), "0s");
  });
});

describe("runRow", () => {
  it("shows an announced-but-unstarted check as queued, not as 0%", () => {
    // The girder check only begins once panels are done; showing it as queued
    // from the outset beats having a second bar appear out of nowhere.
    const row = runRow(run({ started: false }));
    assert.equal(row.state, "pending");
    assert.equal(row.right, "queued");
    assert.equal(row.pct, null);
  });

  it("counts published cases while the check runs", () => {
    const row = runRow(run({ started: true, cases_ready: ["9", "10", "11"] }));
    assert.equal(row.state, "active");
    assert.equal(row.right, "3 / 84");
    assert.equal(row.pct, 4);
  });

  it("says how much a finished check loaded and how long it took", () => {
    const row = runRow(
      run({ complete: true, cases_ready: ["9"], cases_total: 84, elapsed_s: 242 }),
    );
    assert.equal(row.state, "done");
    assert.equal(row.right, "84 cases loaded · 4m 02s");
    assert.equal(row.pct, 100);
  });
});

describe("stepRow", () => {
  it("fills a bar for a step that can count its work", () => {
    assert.deepEqual(stepRow(step({ completed: 56, total: 100 })), {
      right: "56%",
      pct: 56,
    });
  });

  it("goes indeterminate rather than inventing a percentage", () => {
    // Reading a SIN or merging panel fields reports no total; the bar runs as a
    // travelling sliver so "working" never reads as "finished".
    assert.deepEqual(stepRow(step({ total: 0 })), { right: "working", pct: null });
  });
});

describe("CapacityRunStatus", () => {
  const render = (p: CapacityCalcProgress) =>
    renderToStaticMarkup(React.createElement(CapacityRunStatus, { progress: p }));

  it("lists every check from the first frame, running or queued", () => {
    // Nothing may appear out of nowhere mid-run: both checks are on screen
    // before either has produced a case.
    const html = render(
      progress({
        runs: [
          run({ started: true, cases_ready: ["9"] }),
          run({
            id: "run-002",
            scope: "girder",
            label: "DNV-RP-C201 girder",
            started: false,
          }),
        ],
      }),
    );
    assert.ok(html.includes("DNV-RP-C201 stiffened panel"));
    assert.ok(html.includes("DNV-RP-C201 girder"));
    assert.ok(html.includes("queued"));
    assert.ok(html.includes("32 / 84 cases"));
  });

  it("shows the running step and folds the finished ones into a history", () => {
    const html = render(
      progress({
        prep: {
          complete: false,
          active: "Recovering reusable stress fields for basic cases",
          steps: [
            step({ label: "Reading SIN", done: true, elapsed_s: 31.3 }),
            step({ completed: 56, total: 100 }),
          ],
        },
      }),
    );
    assert.ok(html.includes("Recovering reusable stress fields for basic cases"));
    assert.ok(html.includes("56%"));
    // The finished step is behind the collapsed "Completed" toggle.
    assert.ok(html.includes("Completed (1)"));
    assert.ok(!html.includes("Reading SIN"));
  });

  it("stays on screen when the run finishes, collapsed and marked complete", () => {
    // Clearing it at the moment the results land would rearrange the panel
    // under the user, which is the confusing part.
    const html = render(
      progress({
        complete: true,
        cases_done: 84,
        runs: [run({ started: true, complete: true, elapsed_s: 242 })],
      }),
    );
    assert.ok(html.includes("Code check run"));
    assert.ok(html.includes("Complete"));
    assert.ok(html.includes("All result cases calculated"));
    // Collapsed: the detail rows are not rendered, the headline bar still is.
    assert.ok(!html.includes("84 cases loaded"));
    assert.ok(html.includes("84 / 84 cases"));
  });

  it("runs the headline bar indeterminate until the cases are known", () => {
    const html = render(
      progress({
        cases_known: false,
        cases_total: 0,
        cases_done: 0,
        message: "Reading the SIN and reconstructing capacity models",
        runs: [],
      }),
    );
    assert.ok(html.includes("ada-bar-indeterminate"));
    assert.ok(html.includes("Reading the SIN and reconstructing capacity models"));
    assert.ok(!html.includes("0 / 0"));
  });
});

describe("capacity panel visibility", () => {
  it("stays on screen for a streaming run that has computed nothing yet", () => {
    // The state a --stream-results run opens in: viewer up, models being read,
    // no spine to load. Keying the panel on results alone hid the run status
    // for the whole calculation.
    assert.equal(
      shouldShowCapacityPanel({
        hasResults: false,
        loading: false,
        error: false,
        calculating: true,
      }),
      true,
    );
  });

  it("stays hidden for a model with no code check at all", () => {
    assert.equal(
      shouldShowCapacityPanel({
        hasResults: false,
        loading: false,
        error: false,
        calculating: false,
      }),
      false,
    );
  });

  it("shows a finished bundle, and a load in progress or failed", () => {
    const base = { hasResults: false, loading: false, error: false, calculating: false };
    assert.equal(shouldShowCapacityPanel({ ...base, hasResults: true }), true);
    assert.equal(shouldShowCapacityPanel({ ...base, loading: true }), true);
    assert.equal(shouldShowCapacityPanel({ ...base, error: true }), true);
  });
});
