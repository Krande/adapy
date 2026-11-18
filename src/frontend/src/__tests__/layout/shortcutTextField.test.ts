// Which focused elements keep the keys to themselves: a shortcut pressed while a text
// field has focus is the field's, one pressed on a just-ticked checkbox is a shortcut.

import assert from "node:assert/strict";
import { test } from "node:test";

import { isTextField } from "@/layout/shortcutDispatch";

const el = (tagName: string, extra: Record<string, unknown> = {}) => ({ tagName, ...extra }) as unknown as EventTarget;

test("text fields, selects and editable regions take the keys", () => {
  assert.equal(isTextField(el("INPUT")), true, "an input with no type is a text input");
  assert.equal(isTextField(el("input", { type: "text" })), true);
  assert.equal(isTextField(el("INPUT", { type: "number" })), true);
  assert.equal(isTextField(el("INPUT", { type: "search" })), true);
  assert.equal(isTextField(el("TEXTAREA")), true);
  assert.equal(isTextField(el("SELECT")), true);
  assert.equal(isTextField(el("DIV", { isContentEditable: true })), true);
});

test("a checkbox, a radio, a button or a slider does not: Ctrl+S after ticking a box saves", () => {
  for (const type of ["checkbox", "radio", "button", "submit", "range", "color"]) assert.equal(isTextField(el("INPUT", { type })), false, type);
  assert.equal(isTextField(el("BUTTON")), false);
  assert.equal(isTextField(el("DIV")), false);
  assert.equal(isTextField(null), false);
});
