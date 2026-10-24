import assert from "node:assert/strict";
import { test } from "node:test";

import { isTextEntryTarget, undoRedoIntent, type UndoRedoKeyEvent } from "@/utils/keyboard/undoRedoKeys";

// The viewer's Ctrl+Z / Ctrl+Y: which presses step the history, and that typing in a field keeps
// the field's own undo.

const canvas = { tagName: "CANVAS", isContentEditable: false };

function press(key: string, mods: Partial<UndoRedoKeyEvent> = {}): UndoRedoKeyEvent {
  return {
    key,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    altKey: false,
    target: canvas as unknown as EventTarget,
    defaultPrevented: false,
    ...mods,
  };
}

test("Ctrl+Z undoes; Ctrl+Y and Ctrl+Shift+Z redo; Cmd works the same", () => {
  assert.equal(undoRedoIntent(press("z", { ctrlKey: true })), "undo");
  assert.equal(undoRedoIntent(press("y", { ctrlKey: true })), "redo");
  assert.equal(undoRedoIntent(press("Z", { ctrlKey: true, shiftKey: true })), "redo");
  assert.equal(undoRedoIntent(press("z", { metaKey: true })), "undo");
  assert.equal(undoRedoIntent(press("y", { metaKey: true })), "redo");
  assert.equal(undoRedoIntent(press("z", { metaKey: true, shiftKey: true })), "redo");
});

test("other presses are not undo / redo", () => {
  assert.equal(undoRedoIntent(press("z")), null, "a bare Z");
  assert.equal(undoRedoIntent(press("z", { shiftKey: true })), null, "Shift+Z");
  assert.equal(undoRedoIntent(press("z", { ctrlKey: true, altKey: true })), null, "Ctrl+Alt+Z");
  assert.equal(undoRedoIntent(press("c", { ctrlKey: true })), null, "Ctrl+C");
  assert.equal(undoRedoIntent(press("y", { ctrlKey: true, shiftKey: true })), null, "Ctrl+Shift+Y");
});

test("ignored while typing in an input, textarea, select or contentEditable", () => {
  for (const target of [
    { tagName: "INPUT", isContentEditable: false },
    { tagName: "TEXTAREA", isContentEditable: false },
    { tagName: "SELECT", isContentEditable: false },
    { tagName: "DIV", isContentEditable: true },
  ]) {
    assert.ok(isTextEntryTarget(target), target.tagName);
    assert.equal(undoRedoIntent(press("z", { ctrlKey: true, target: target as unknown as EventTarget })), null);
    assert.equal(undoRedoIntent(press("y", { ctrlKey: true, target: target as unknown as EventTarget })), null);
  }
  assert.ok(!isTextEntryTarget(canvas));
  assert.ok(!isTextEntryTarget(null));
});

test("ignored when an earlier handler already took the press", () => {
  assert.equal(undoRedoIntent(press("z", { ctrlKey: true, defaultPrevented: true })), null);
});
