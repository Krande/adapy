// Shortcuts a shell or a plugin registers: parsed, matched, refused on core's
// own keys or a taken chord, and a non-core panel's `shortcut` toggles it.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { registerPanel, resetPanelRegistry } from "@/layout/registry";
import { listShortcuts, matchShortcut, parseShortcut, registerShortcut, resetShortcuts } from "@/layout/shortcuts";

const press = (key: string, mods: { shift?: boolean; ctrl?: boolean; meta?: boolean; alt?: boolean } = {}) => ({
  key,
  shiftKey: !!mods.shift,
  ctrlKey: !!mods.ctrl,
  metaKey: !!mods.meta,
  altKey: !!mods.alt,
});
const body = () => Promise.resolve({ default: () => null });

beforeEach(() => {
  resetShortcuts();
  resetPanelRegistry();
});

test("a chord parses into its key and modifiers; Cmd is Ctrl", () => {
  assert.deepEqual(parseShortcut("Shift+K"), { key: "k", shift: true, ctrl: false, alt: false });
  assert.deepEqual(parseShortcut("Cmd+Alt+M"), { key: "m", shift: false, ctrl: true, alt: true });
  assert.equal(parseShortcut("Shift+"), null);
});

test("a registered shortcut matches its chord only, and only while live", () => {
  let live = true;
  let ran = 0;
  assert.equal(registerShortcut({ id: "x", keys: "Shift+K", label: "Do X", run: () => void ran++, when: () => live }, "shell"), true);
  const list = listShortcuts();
  assert.equal(matchShortcut(press("K", { shift: true }), list)?.id, "x", "Shift gives the upper-case key");
  assert.equal(matchShortcut(press("k"), list), null);
  assert.equal(matchShortcut(press("K", { shift: true, ctrl: true }), list), null);
  live = false;
  assert.equal(matchShortcut(press("K", { shift: true }), list), null);
  assert.equal(ran, 0, "matching does not run");
});

test("core's own keys and a taken chord are refused; the same id replaces its own", () => {
  assert.equal(registerShortcut({ id: "h", keys: "Shift+H", label: "", run() {} }, "p"), false);
  assert.equal(registerShortcut({ id: "z", keys: "Ctrl+Z", label: "", run() {} }, "p"), false);
  assert.equal(registerShortcut({ id: "a", keys: "Shift+K", label: "A", run() {} }, "p"), true);
  assert.equal(registerShortcut({ id: "b", keys: "shift+k", label: "B", run() {} }, "p"), false);
  assert.equal(registerShortcut({ id: "a", keys: "Shift+L", label: "A2", run() {} }, "p"), true);
  assert.deepEqual(listShortcuts().filter((s) => s.owner !== "core").map((s) => [s.id, s.keys]), [["a", "Shift+L"]]);
});

test("a non-core panel's shortcut toggles it; core's panels keep their own handlers", () => {
  registerPanel({ id: "p:one", title: "One", body, shortcut: "Shift+O" }, "p");
  registerPanel({ id: "p:clash", title: "Clash", body, shortcut: "Shift+T" }, "p");
  registerPanel({ id: "core:x", title: "X", body, shortcut: "Shift+X" }, "core");
  const toggled: string[] = [];
  const list = listShortcuts((id) => toggled.push(id));
  assert.deepEqual(list.filter((s) => s.owner !== "core").map((s) => s.id), ["panel:p:one"], "a reserved chord and core's panels are left out");
  matchShortcut(press("O", { shift: true }), list)!.run();
  assert.deepEqual(toggled, ["p:one"]);
  assert.equal(list.find((s) => s.id === "panel:p:one")!.label, "Show or hide One");
});
