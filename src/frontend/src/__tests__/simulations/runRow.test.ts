// What a run's one line in the runs list says.
import assert from "node:assert/strict";
import { test } from "node:test";

import { durationLabel, runLabel, runOutcome, startedLabel } from "@/services/simulations/runRow";

const provider = { id: "p1", domain: "robotics", engine: "e", version: "1" } as const;

test("a run is called by the label it was started with, then its summary's, then its provider", () => {
  assert.equal(runLabel({ parameters: { label: " night shift " }, summary: { label: "other" }, provider }, { title: "Cell" }), "night shift");
  assert.equal(runLabel({ parameters: {}, summary: { label: "from summary" }, provider }, { title: "Cell" }), "from summary");
  assert.equal(runLabel({ parameters: { label: 7 }, summary: {}, provider }, { title: "Cell" }), "Cell");
  assert.equal(runLabel({ parameters: {}, summary: {}, provider }, null), "p1");
});

test("the outcome is the summary's status line, when it is text", () => {
  assert.equal(runOutcome({ summary: { status: "Done: 4 of 4" } }), "Done: 4 of 4");
  assert.equal(runOutcome({ summary: { status: 3 } }), "");
  assert.equal(runOutcome({ summary: {} }), "");
});

test("how long a run took reads in the unit that fits", () => {
  assert.equal(durationLabel("2026-01-01T10:00:00Z", "2026-01-01T10:00:42Z"), "42 s");
  assert.equal(durationLabel("2026-01-01T10:00:00Z", "2026-01-01T10:07:15Z"), "7 min 15 s");
  assert.equal(durationLabel("2026-01-01T10:00:00Z", "2026-01-01T12:05:00Z"), "2 h 5 min");
  assert.equal(durationLabel(null, "2026-01-01T10:00:42Z"), "");
  assert.equal(durationLabel("2026-01-01T10:00:00Z", null), "");
  assert.equal(durationLabel("2026-01-01T10:00:42Z", "2026-01-01T10:00:00Z"), "", "out of order says nothing");
  assert.equal(durationLabel("nonsense", "2026-01-01T10:00:00Z"), "");
});

test("when a run started is its time today and its date before", () => {
  const now = new Date(2026, 5, 10, 15, 0, 0);
  const today = startedLabel(new Date(2026, 5, 10, 9, 5, 0).toISOString(), now);
  const earlier = startedLabel(new Date(2026, 5, 8, 9, 5, 0).toISOString(), now);
  assert.match(today, /^\d{1,2}[:.]\d{2}/);
  assert.ok(earlier.length > today.length && earlier.endsWith(today), `${earlier} / ${today}`);
  assert.equal(startedLabel("nonsense", now), "");
});
