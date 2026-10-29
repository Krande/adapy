import assert from "node:assert/strict";
import { test } from "node:test";

import { isAdditiveClick } from "@/utils/mesh_select/additiveClick";

test("Shift, Ctrl and Cmd add a clicked element to the selection; a plain click replaces it", () => {
  const click = (mods: Partial<Record<"shiftKey" | "ctrlKey" | "metaKey", boolean>>) =>
    isAdditiveClick({ shiftKey: false, ctrlKey: false, metaKey: false, ...mods });
  assert.equal(click({}), false);
  assert.equal(click({ shiftKey: true }), true);
  assert.equal(click({ ctrlKey: true }), true);
  assert.equal(click({ metaKey: true }), true);
});
