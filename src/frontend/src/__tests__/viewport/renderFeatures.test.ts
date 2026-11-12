// The optional device features the WebGPU renderer asks for: only those the adapter offers, so a
// device request never fails for a feature the adapter does not have.

import assert from "node:assert/strict";
import { test } from "node:test";

const { renderFeatures } = await import("@/viewport/adacpp/rendererModule");

const adapter = (names: string[]) => ({ features: new Set(names) });

test("the float depth + stencil target is requested where the adapter offers it", () => {
  assert.deepEqual(renderFeatures(adapter(["depth32float-stencil8", "timestamp-query"])), ["depth32float-stencil8"]);
});

test("nothing is requested that the adapter does not offer", () => {
  assert.deepEqual(renderFeatures(adapter(["timestamp-query"])), []);
  assert.deepEqual(renderFeatures({}), [], "an adapter that lists no features");
});
