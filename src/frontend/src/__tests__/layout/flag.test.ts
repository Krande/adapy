import assert from "node:assert/strict";
import { test } from "node:test";

import { LAYOUT_ENGINE_STORAGE_KEY, layoutEngineEnabled } from "@/layout/flag";

// The layout-engine flag: off unless asked for, the URL over storage.

const store = (v: string | null) => ({ getItem: (k: string) => (k === LAYOUT_ENGINE_STORAGE_KEY ? v : null) });

test("off by default", () => {
  assert.equal(layoutEngineEnabled("", null), false);
  assert.equal(layoutEngineEnabled("?ui=core", store(null)), false);
});

test("?layout=engine turns it on for the page load", () => {
  assert.equal(layoutEngineEnabled("?ui=core&layout=engine", null), true);
  assert.equal(layoutEngineEnabled("?layout=ENGINE", null), true);
});

test("the stored flag turns it on until removed", () => {
  assert.equal(layoutEngineEnabled("", store("on")), true);
  assert.equal(layoutEngineEnabled("", store("1")), true);
  assert.equal(layoutEngineEnabled("", store("nonsense")), false);
});

test("the URL wins over storage, both ways", () => {
  assert.equal(layoutEngineEnabled("?layout=classic", store("on")), false);
  assert.equal(layoutEngineEnabled("?layout=engine", store("off")), true);
  // An unknown value in the URL decides nothing; storage does.
  assert.equal(layoutEngineEnabled("?layout=maybe", store("on")), true);
});

test("blocked storage reads as off", () => {
  const blocked = {
    getItem(): string | null {
      throw new Error("SecurityError");
    },
  };
  assert.equal(layoutEngineEnabled("", blocked), false);
});

test("without a window it is off", () => {
  assert.equal(typeof (globalThis as { window?: unknown }).window, "undefined");
  assert.equal(layoutEngineEnabled(), false);
});
