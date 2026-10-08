import assert from "node:assert/strict";
import { test } from "node:test";

import { isLightColor } from "../../utils/colorLuminance";

test("light panel text means a dark panel (and dark native controls)", () => {
  // the shipped presets' text colours
  assert.equal(isLightColor("#f1f5f9"), true); // Slate glass
  assert.equal(isLightColor("#f3f4f6"), true); // Dark
  assert.equal(isLightColor("#ffffff"), true); // Pale glass
  assert.equal(isLightColor("#111827"), false); // Mist
  assert.equal(isLightColor("#fff"), true);
  assert.equal(isLightColor("rgba(10, 10, 10, 0.9)"), false);
  assert.equal(isLightColor("rgb(240, 240, 240)"), true);
  assert.equal(isLightColor("not-a-colour"), false);
});
