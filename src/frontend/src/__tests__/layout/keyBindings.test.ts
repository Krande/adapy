// The user's keyboard shortcuts: defaults unchanged with no overrides, one effective table for every
// reader, assigning (conflicts, refusals), unbinding, resetting, and the saved prefs round trip.

import assert from "node:assert/strict";
import { test, beforeEach } from "node:test";

await import("../fixtures/panelRealm");

const S = await import("@/layout/shortcuts");
const K = await import("@/layout/keyBindings");
const C = await import("@/layout/commands");
const { CORE_SHORTCUTS } = await import("@/layout/coreShortcutTable");
const { layoutStore, createLayoutStore, memoryLayoutPersistence, migrateLayoutPrefs, LAYOUT_PREFS_VERSION } = await import("@/layout/layoutStore");

beforeEach(() => {
  S.resetShortcuts();
  C.resetCommands();
  layoutStore.getState().resetKeyOverrides();
});

const press = (keys: string) => {
  const c = S.parseShortcut(keys)!;
  return { key: c.key, shiftKey: c.shift, ctrlKey: c.ctrl, metaKey: false, altKey: c.alt };
};
const bound = () => {
  let n = 0;
  const off = S.bindCoreShortcuts({ "core:select-all": { run: () => void n++ }, "core:fit-all": { run: () => void n++ } });
  return { count: () => n, off };
};

test("with no overrides the effective table is core's table plus what is registered", () => {
  C.registerCommand({ id: "t:cmd", label: "T", keys: "Ctrl+Alt+Y", run: () => {} }, "plug");
  const list = S.listShortcuts();
  for (const d of CORE_SHORTCUTS) {
    const hit = list.find((s) => s.id === d.id)!;
    assert.equal(hit.keys, d.keys, d.id);
  }
  assert.equal(list.find((s) => s.id === "cmd:t:cmd")!.keys, "Ctrl+Alt+Y");
  assert.deepEqual(
    list.map((s) => [s.id, s.keys]),
    S.listDefaultShortcuts().map((s) => [s.id, s.keys]),
  );
  assert.equal(S.coreShortcutKeys("core:select-all"), "Ctrl+A");
});

test("an override moves a core key: the new chord fires, the old one does not, and every reader shows it", () => {
  const b = bound();
  assert.ok(K.assignKeys("core:select-all", "Ctrl+Shift+Y").ok);
  const list = S.listShortcuts();
  assert.equal(S.matchShortcut(press("Ctrl+Shift+Y"), list)?.id, "core:select-all");
  assert.equal(S.matchShortcut(press("Ctrl+A"), list), null);
  assert.equal(S.coreShortcutKeys("core:select-all"), "Ctrl+Shift+Y");
  assert.equal(C.commandChord({ shortcut: "core:select-all" }), "Ctrl+Shift+Y", "the palette and tooltips read the same");
  const row = K.keyRows().find((r) => r.shortcutId === "core:select-all")!;
  assert.deepEqual([row.keys, row.defaultKeys, row.overridden], ["Ctrl+Shift+Y", "Ctrl+A", true]);
  b.off();
});

test("reset and unbind", () => {
  assert.ok(K.assignKeys("core:select-all", "Ctrl+Shift+Y").ok);
  K.setKeyBinding("core:select-all", undefined);
  assert.equal(S.coreShortcutKeys("core:select-all"), "Ctrl+A");
  assert.equal(layoutStore.getState().prefs.keyOverrides, undefined, "a reset leaves nothing stored");
  K.setKeyBinding("core:select-all", null);
  assert.equal(S.coreShortcutKeys("core:select-all"), undefined);
  assert.equal(S.listShortcuts().some((s) => s.id === "core:select-all"), false);
  assert.ok(K.assignKeys("core:fit-all", "Ctrl+Shift+Y").ok);
  K.resetAllKeyBindings();
  assert.equal(S.coreShortcutKeys("core:select-all"), "Ctrl+A");
  assert.equal(S.coreShortcutKeys("core:fit-all"), "Shift+A");
});

test("a chord already in use is a conflict; replace takes it and leaves the holder unbound", () => {
  const res = K.assignKeys("core:fit-all", "Ctrl+A");
  assert.equal(res.ok, false);
  assert.equal(res.ok === false && res.conflict?.id, "core:select-all");
  assert.equal(S.coreShortcutKeys("core:fit-all"), "Shift+A", "nothing changed");
  assert.ok(K.assignKeys("core:fit-all", "Ctrl+A", { replace: true }).ok);
  assert.equal(S.coreShortcutKeys("core:fit-all"), "Ctrl+A");
  assert.equal(S.coreShortcutKeys("core:select-all"), undefined);
});

test("what the browser or the OS keeps is refused; core's own keys are the user's to move", () => {
  for (const k of ["Ctrl+W", "Ctrl+T", "Ctrl+N", "Ctrl+Shift+N", "Alt+F4", "F5", "Ctrl+Tab"]) {
    assert.match(K.refuseChord(k) ?? "", /reserved by the browser/, k);
    assert.equal(K.assignKeys("core:fit-all", k).ok, false);
  }
  assert.match(K.refuseChord("Escape") ?? "", /page itself/);
  assert.match(K.refuseChord("Shift") ?? "", /modifier/);
  assert.equal(K.refuseChord("Ctrl+Shift+K"), null);
  // Shift+H is core's (refused to plugins): the user may take it.
  assert.ok(S.RESERVED_SHORTCUTS.includes("Shift+H"));
  assert.ok(K.assignKeys("core:fit-all", "Shift+H", { replace: true }).ok);
  assert.equal(S.coreShortcutKeys("core:fit-all"), "Shift+H");
});

test("an override for an id nothing registers is ignored", () => {
  const before = S.listShortcuts().map((s) => [s.id, s.keys]);
  S.setShortcutOverrides({ "nope:nothing": "Ctrl+Shift+Y", "cmd:nope": "Ctrl+Alt+Y" });
  assert.deepEqual(S.listShortcuts().map((s) => [s.id, s.keys]), before);
});

test("a command with no default key can be given one, and runs", () => {
  let ran = 0;
  C.registerCommand({ id: "t:plain", label: "Plain", run: () => void ran++ }, "plug");
  const sid = K.shortcutIdOf({ id: "t:plain" });
  assert.equal(sid, "cmd:t:plain");
  assert.ok(K.assignKeys(sid, "Ctrl+Alt+J").ok);
  const hit = S.matchShortcut(press("Ctrl+Alt+J"), S.listShortcuts());
  assert.equal(hit?.id, sid);
  hit!.run();
  assert.equal(ran, 1);
  const row = K.keyRows().find((r) => r.shortcutId === sid)!;
  assert.equal(row.keys, "Ctrl+Alt+J");
  assert.equal(row.defaultKeys, undefined);
  // A registered default can be moved and unbound as well.
  C.registerCommand({ id: "t:keyed", label: "Keyed", keys: "Ctrl+Alt+K", run: () => {} }, "plug");
  assert.ok(K.assignKeys("cmd:t:keyed", "Ctrl+Alt+L").ok);
  assert.equal(S.matchShortcut(press("Ctrl+Alt+K"), S.listShortcuts()), null);
  assert.equal(S.matchShortcut(press("Ctrl+Alt+L"), S.listShortcuts())?.id, "cmd:t:keyed");
});

test("a widget's own (local) key is listed but cannot be moved", () => {
  const row = K.keyRows().find((r) => r.shortcutId === "core:builder-translate")!;
  assert.equal(row.locked, true);
  S.setShortcutOverrides({ "core:builder-translate": "Ctrl+Shift+Y" });
  assert.equal(S.coreShortcutKeys("core:builder-translate"), "G");
});

test("the overrides are saved in the layout prefs and come back", async () => {
  const backend = memoryLayoutPersistence();
  const a = createLayoutStore();
  await a.getState().hydrate(backend);
  a.getState().setKeyOverride("core:select-all", "Ctrl+Shift+Y");
  a.getState().setKeyOverride("core:fit-all", null);
  assert.deepEqual(backend.saved!.keyOverrides, { "core:select-all": "Ctrl+Shift+Y", "core:fit-all": null });
  const b = createLayoutStore();
  await b.getState().hydrate(backend);
  assert.deepEqual(b.getState().prefs.keyOverrides, { "core:select-all": "Ctrl+Shift+Y", "core:fit-all": null });
  const junk = migrateLayoutPrefs({ v: LAYOUT_PREFS_VERSION, docs: {}, keyOverrides: { a: "Ctrl+K", b: 3, c: null, d: "" } });
  assert.deepEqual(junk.keyOverrides, { a: "Ctrl+K", c: null });
  // Not per shell: no shell id in the key.
  assert.equal(JSON.stringify(backend.saved).includes("ByShell\":{\"core:select-all"), false);
});

test("chords are captured in the registry's spelling", () => {
  const ev = (key: string, m: Partial<Record<"ctrlKey" | "metaKey" | "altKey" | "shiftKey", boolean>> = {}) => ({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...m });
  assert.equal(K.chordFromEvent(ev("k", { ctrlKey: true, shiftKey: true })), "Ctrl+Shift+K");
  assert.equal(K.chordFromEvent(ev("ArrowUp", { shiftKey: true })), "Shift+ArrowUp");
  assert.equal(K.chordFromEvent(ev("Shift", { shiftKey: true })), null);
  assert.equal(K.normaliseChord("Shift+Ctrl+k"), K.normaliseChord("Ctrl+Shift+K"));
});

test("the shortcuts dialog, the dispatcher and the rail all read the one effective table", async () => {
  const { readFileSync } = await import("node:fs");
  const read = (p: string) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
  assert.match(read("layout/shortcutDispatch.ts"), /listShortcuts\(/);
  assert.match(read("layout/keyBindings.ts"), /listShortcuts|listDefaultShortcuts/);
  assert.match(read("layout/commands.ts"), /coreShortcutKeys\(c\.shortcut\)/);
});
