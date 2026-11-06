import assert from "node:assert/strict";
import { test } from "node:test";

import { bodyPaletteCss } from "@/components/ui/panelChrome";

test("a body palette is scoped to panel bodies, custom properties only, values that cannot break out", () => {
  assert.equal(
    bodyPaletteCss({ "--color-gray-800": "#111", "--color-blue-600": "oklch(0.6 0.2 250)" }),
    "[data-ada-panel-body]{--color-gray-800:#111;--color-blue-600:oklch(0.6 0.2 250);}",
  );
  assert.equal(bodyPaletteCss({ color: "red", "--x": "red;} body{display:none" }), "", "not a custom property; a value that closes the rule");
  assert.equal(bodyPaletteCss(null), "");
});
