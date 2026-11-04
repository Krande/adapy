import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BREAKPOINTS,
  COMPACT_QUERY,
  NARROW_QUERY,
  WIDE_QUERY,
  mediaMatches,
} from "@/layout/breakpoints";

// The queries the panels spelled out by hand before there was one module for
// them. They must not move: a sheet, a window and the top bar switch exactly
// where they always did, in step with Tailwind's `sm:` and `md:` classes.

test("the named queries are the ones the panels used", () => {
  assert.equal(COMPACT_QUERY, "(max-width: 639px)");
  assert.equal(NARROW_QUERY, "(max-width: 767px)");
  assert.equal(WIDE_QUERY, "(min-width: 768px)");
});

test("the breakpoints are Tailwind's sm and md", () => {
  assert.deepEqual({ ...BREAKPOINTS }, { compact: 640, wide: 768 });
  assert.ok(Object.isFrozen(BREAKPOINTS));
});

test("without a window nothing matches", () => {
  assert.equal(typeof (globalThis as { window?: unknown }).window, "undefined");
  assert.equal(mediaMatches(COMPACT_QUERY), false);
});

test("with a window the query is asked of matchMedia", () => {
  const asked: string[] = [];
  const g = globalThis as { window?: unknown };
  g.window = { matchMedia: (q: string) => (asked.push(q), { matches: q === NARROW_QUERY }) };
  try {
    assert.equal(mediaMatches(NARROW_QUERY), true);
    assert.equal(mediaMatches(WIDE_QUERY), false);
    assert.deepEqual(asked, [NARROW_QUERY, WIDE_QUERY]);
  } finally {
    delete g.window;
  }
});
