import assert from "node:assert/strict";
import { test } from "node:test";

import { LAYOUT_ENGINE_STORAGE_KEY, layoutEngineEnabled } from "@/layout/flag";

// The retired layout-engine flag: kept for the facade, always on.

const store = (v: string | null) => ({ getItem: (k: string) => (k === LAYOUT_ENGINE_STORAGE_KEY ? v : null) });

test("always on: the layout engine is the only path", () => {
  assert.equal(layoutEngineEnabled(), true);
  assert.equal(layoutEngineEnabled("", null), true);
});

test("?layout=classic and the stored flag no longer turn it off", () => {
  assert.equal(layoutEngineEnabled("?ui=core&layout=classic", null), true);
  assert.equal(layoutEngineEnabled("?layout=off", store("off")), true);
  assert.equal(layoutEngineEnabled("", store("0")), true);
});
