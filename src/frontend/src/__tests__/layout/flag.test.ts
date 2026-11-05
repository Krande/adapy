import assert from "node:assert/strict";
import { test } from "node:test";

import { LAYOUT_ENGINE_STORAGE_KEY, layoutEngineEnabled } from "@/layout/flag";

// The layout-engine flag: on unless turned off, the URL over storage.

const store = (v: string | null) => ({ getItem: (k: string) => (k === LAYOUT_ENGINE_STORAGE_KEY ? v : null) });

test("on by default", () => {
  assert.equal(layoutEngineEnabled("", null), true);
  assert.equal(layoutEngineEnabled("?ui=core", store(null)), true);
});

test("?layout=classic turns it off for the page load", () => {
  assert.equal(layoutEngineEnabled("?ui=core&layout=classic", null), false);
  assert.equal(layoutEngineEnabled("?layout=CLASSIC", null), false);
});

test("the stored flag turns it off until removed", () => {
  assert.equal(layoutEngineEnabled("", store("off")), false);
  assert.equal(layoutEngineEnabled("", store("0")), false);
  assert.equal(layoutEngineEnabled("", store("nonsense")), true);
});

test("the URL wins over storage, both ways", () => {
  assert.equal(layoutEngineEnabled("?layout=classic", store("on")), false);
  assert.equal(layoutEngineEnabled("?layout=engine", store("off")), true);
  // An unknown value in the URL decides nothing; storage does.
  assert.equal(layoutEngineEnabled("?layout=maybe", store("off")), false);
});

test("blocked storage reads as on", () => {
  const blocked = {
    getItem(): string | null {
      throw new Error("SecurityError");
    },
  };
  assert.equal(layoutEngineEnabled("", blocked), true);
});

test("without a window it is on", () => {
  assert.equal(typeof (globalThis as { window?: unknown }).window, "undefined");
  assert.equal(layoutEngineEnabled(), true);
});
