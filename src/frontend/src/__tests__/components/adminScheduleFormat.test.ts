import assert from "node:assert/strict";
import { test } from "node:test";

import { fmtRelative } from "@/components/admin/scheduleFormat";

// Past times read "10m ago", not "ago 10m": the CLI Tokens tab shows when a
// token was issued, last used and revoked, all of which are in the past.

const at = (offsetMs: number) => new Date(Date.now() + offsetMs).toISOString();

test("future times read 'in …'", () => {
  assert.equal(fmtRelative(at(30_000)), "in <1m");
  assert.equal(fmtRelative(at(34 * 60_000 + 5_000)), "in 34m");
  assert.equal(fmtRelative(at(3 * 3600_000 + 60_000)), "in 3h");
  assert.equal(fmtRelative(at(30 * 86400_000 + 60_000)), "in 30d");
});

test("past times read '… ago'", () => {
  assert.equal(fmtRelative(at(-30_000)), "<1m ago");
  assert.equal(fmtRelative(at(-10 * 60_000)), "10m ago");
  assert.equal(fmtRelative(at(-3 * 3600_000)), "3h ago");
  assert.equal(fmtRelative(at(-2 * 86400_000)), "2d ago");
});

test("no time, no text", () => {
  assert.equal(fmtRelative(null), "");
});
