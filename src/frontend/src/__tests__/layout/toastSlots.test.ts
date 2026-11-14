// Toast slots: a shell's or plugin's own toasts in core's one toast stack.

import assert from "node:assert/strict";
import { test } from "node:test";

const S = await import("@/layout/toastSlots");

test("slots are listed in order, replaced by id, and their changes told", () => {
  let told = 0;
  const off = S.subscribeToastSlots(() => told++);
  const before = S.toastSlotsVersion();
  S.registerToastSlot({ id: "b", render: () => "b" }, "shell");
  S.registerToastSlot({ id: "a", order: 1, render: () => "a" });
  S.registerToastSlot({ id: "c", order: -1, render: () => "c" });
  assert.deepEqual(S.listToastSlots().map((s) => s.id), ["c", "b", "a"]);
  S.registerToastSlot({ id: "b", order: 2, render: () => "b2" });
  assert.deepEqual(S.listToastSlots().map((s) => s.id), ["c", "a", "b"]);
  assert.equal(S.listToastSlots()[2].render(), "b2");
  for (const id of ["a", "b", "c"]) S.unregisterToastSlot(id);
  S.unregisterToastSlot("a"); // gone already: nothing to tell
  off();
  assert.deepEqual(S.listToastSlots(), []);
  assert.equal(told, 7);
  assert.equal(S.toastSlotsVersion(), before + 7);
});
