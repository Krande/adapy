// Core's keys live in the shortcut registry: each triggers its action once, a field keeps
// its own keys, a second listener never doubles a press, plugins are refused core's chords,
// and the dialog and the commands' menu chords read the same entries.

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

await import("../fixtures/panelRealm");

const { CORE_SHORTCUTS } = await import("@/layout/coreShortcutTable");
const { coreCommandSpecs } = await import("@/layout/coreCommands");
const { commandChord } = await import("@/layout/commands");
const { installShortcutDispatcher } = await import("@/layout/shortcutDispatch");
const { RESERVED_SHORTCUTS, bindCoreShortcuts, listShortcuts, parseShortcut, registerShortcut, resetShortcuts } = await import("@/layout/shortcuts");
const { shortcutDialogGroups } = await import("@/components/options/ShortcutsModal");
const realWindow = (globalThis as any).window;

type Listener = (e: unknown) => void;
const listeners = new Set<Listener>();
let adds = 0;

beforeEach(() => {
  resetShortcuts();
  listeners.clear();
  adds = 0;
  (globalThis as any).window = {
    addEventListener: (type: string, fn: Listener) => {
      if (type === "keydown") {
        listeners.add(fn);
        adds++;
      }
    },
    removeEventListener: (type: string, fn: Listener) => {
      if (type === "keydown") listeners.delete(fn);
    },
  };
});
afterEach(() => {
  (globalThis as any).window = realWindow;
});

interface Fake {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  repeat: boolean;
  defaultPrevented: boolean;
  target: unknown;
  preventDefault(): void;
}
function press(key: string, mods: Partial<Fake> = {}): Fake {
  const e: Fake = {
    key,
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    repeat: false,
    defaultPrevented: false,
    target: { tagName: "DIV" },
    preventDefault() {
      e.defaultPrevented = true;
    },
    ...mods,
  };
  for (const l of [...listeners]) l(e);
  return e;
}

const dispatched = CORE_SHORTCUTS.filter((d) => !d.local);
const chordOf = (keys: string) => {
  const c = parseShortcut(keys)!;
  return { key: c.key.length === 1 && c.shift ? c.key.toUpperCase() : keyName(c.key), shiftKey: c.shift, ctrlKey: c.ctrl, altKey: c.alt };
};
const keyName = (k: string) => (k.startsWith("arrow") ? "Arrow" + k.slice(5, 6).toUpperCase() + k.slice(6) : k === "enter" ? "Enter" : k);

test("every dispatched core key runs its handler exactly once per press, however many listeners are installed", () => {
  const ran: Record<string, number> = {};
  const handlers = Object.fromEntries(dispatched.map((d) => [d.id, { run: () => void (ran[d.id] = (ran[d.id] ?? 0) + 1) }]));
  bindCoreShortcuts(handlers);
  const offA = installShortcutDispatcher(); // the layout's
  const offB = installShortcutDispatcher(); // a viewer's
  assert.equal(adds, 1, "one window listener, shared");
  for (const d of dispatched) {
    press(chordOf(d.keys).key, chordOf(d.keys));
    assert.equal(ran[d.id], 1, `${d.id} (${d.keys}) ran once`);
  }
  offA();
  assert.equal(listeners.size, 1, "still listening for the second user");
  offB();
  assert.equal(listeners.size, 0);
});

test("an unbound core key does nothing and is left alone; keys that never prevented the default still do not", () => {
  const off = installShortcutDispatcher();
  assert.equal(press("A", { shiftKey: true }).defaultPrevented, false);
  let n = 0;
  bindCoreShortcuts({ "core:fit-all": { run: () => void n++ }, "core:toggle-gallery": { run: () => void n++ } });
  assert.equal(press("A", { shiftKey: true }).defaultPrevented, false, "Shift+A never prevented the default");
  assert.equal(press("W", { shiftKey: true }).defaultPrevented, true, "Shift+W did");
  assert.equal(n, 2);
  off();
});

test("a run that returns false is not handled: no preventDefault", () => {
  bindCoreShortcuts({ "core:toggle-admin": { run: () => false } });
  const off = installShortcutDispatcher();
  assert.equal(press("P", { shiftKey: true }).defaultPrevented, false);
  off();
});

test("a key typed into a field, or already handled, is not a shortcut", () => {
  let n = 0;
  bindCoreShortcuts({ "core:undo": { run: () => void n++ }, "core:show-all": { run: () => void n++ } });
  const off = installShortcutDispatcher();
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    press("z", { ctrlKey: true, target: { tagName } });
    press("U", { shiftKey: true, target: { tagName } });
  }
  press("z", { ctrlKey: true, target: { tagName: "DIV", isContentEditable: true } });
  press("z", { ctrlKey: true, defaultPrevented: true });
  assert.equal(n, 0);
  assert.equal(press("z", { ctrlKey: true }).defaultPrevented, true);
  assert.equal(press("z", { metaKey: true }).defaultPrevented, true, "Cmd is Ctrl");
  assert.equal(n, 2);
  off();
});

test("a held key repeats for core's keys but not for a plugin's", () => {
  let core = 0;
  let plug = 0;
  bindCoreShortcuts({ "core:select-next-sibling": { run: () => void core++ } });
  assert.equal(registerShortcut({ id: "p", keys: "Shift+K", label: "", run: () => void plug++ }, "plugin"), true);
  const off = installShortcutDispatcher();
  press("ArrowRight", { shiftKey: true, repeat: true });
  press("K", { shiftKey: true, repeat: true });
  assert.deepEqual([core, plug], [1, 0]);
  press("K", { shiftKey: true });
  assert.equal(plug, 1);
  off();
});

test("a handler's own condition scopes the key, and the key event reaches it", () => {
  let inScope = false;
  let n = 0;
  bindCoreShortcuts({
    "core:select-parent": { run: () => void n++, when: (e) => inScope && !(e?.target as any)?.closestTree },
  });
  const off = installShortcutDispatcher();
  press("ArrowUp", { shiftKey: true });
  assert.equal(n, 0, "out of scope");
  inScope = true;
  press("ArrowUp", { shiftKey: true, target: { tagName: "DIV", closestTree: true } });
  assert.equal(n, 0, "inside a tree the arrows are the tree's");
  press("ArrowUp", { shiftKey: true });
  assert.equal(n, 1);
  off();
});

test("two viewers bound at once each answer for their own scope; unbinding removes only its own", () => {
  const ran: string[] = [];
  const offA = bindCoreShortcuts({ "core:show-all": { run: () => void ran.push("a"), when: () => false } });
  const offB = bindCoreShortcuts({ "core:show-all": { run: () => void ran.push("b") } });
  const off = installShortcutDispatcher();
  press("U", { shiftKey: true });
  assert.deepEqual(ran, ["b"]);
  offB();
  offA();
  press("U", { shiftKey: true });
  assert.deepEqual(ran, ["b"], "nothing bound, nothing runs");
  off();
});

test("local core keys are listed and reserved but never dispatched", () => {
  const locals = CORE_SHORTCUTS.filter((d) => d.local);
  assert.ok(locals.length > 0);
  const listed = new Map(listShortcuts().map((s) => [s.id, s]));
  for (const d of locals) assert.equal(listed.get(d.id)?.local, true, d.id);
  const off = installShortcutDispatcher();
  assert.equal(press("Escape").defaultPrevented, false);
  assert.equal(press("F6").defaultPrevented, false);
  off();
});

test("the table has no two dispatched entries on one chord, and unique ids under core:", () => {
  const seen = new Map<string, string>();
  for (const d of dispatched) {
    const c = parseShortcut(d.keys)!;
    const k = `${c.ctrl}${c.alt}${c.shift}${c.key}`;
    assert.equal(seen.get(k), undefined, `${d.id} shares ${d.keys} with ${seen.get(k)}`);
    seen.set(k, d.id);
  }
  assert.equal(new Set(CORE_SHORTCUTS.map((d) => d.id)).size, CORE_SHORTCUTS.length);
  assert.ok(CORE_SHORTCUTS.every((d) => d.id.startsWith("core:")));
});

test("RESERVED_SHORTCUTS is derived from the registry: plugins are refused every dispatched core chord", () => {
  for (const d of dispatched) assert.ok(RESERVED_SHORTCUTS.includes(d.keys), d.keys);
  for (const d of dispatched) assert.equal(registerShortcut({ id: `x:${d.id}`, keys: d.keys, label: "", run() {} }, "plugin"), false, d.keys);
  assert.equal(registerShortcut({ id: "x:esc", keys: "Escape", label: "", run() {} }, "plugin"), false);
  assert.equal(registerShortcut({ id: "x:f6", keys: "F6", label: "", run() {} }, "plugin"), false);
  // Mode-local keys are not reserved: nothing blocked a plugin on them before.
  assert.equal(registerShortcut({ id: "x:g", keys: "G", label: "", run() {} }, "plugin"), true);
});

test("core commands show the registered chord of the shortcut they name", () => {
  const named = coreCommandSpecs().filter((s) => s.shortcut);
  assert.ok(named.length >= 10);
  const keys = new Map(CORE_SHORTCUTS.map((d) => [d.id, d.keys]));
  for (const s of named) {
    assert.ok(keys.has(s.shortcut!), `${s.id} names a real shortcut`);
    assert.equal(s.chordLabel, undefined, `${s.id} has no hand-written chord`);
    assert.equal(commandChord(s), keys.get(s.shortcut!));
  }
  const undo = coreCommandSpecs().find((s) => s.id.endsWith("undo"))!;
  assert.equal(commandChord(undo), "Ctrl+Z");
});

test("the dialog lists every registered shortcut, grouped, with no hand-written duplicates", () => {
  registerShortcut({ id: "x:k", keys: "Shift+K", label: "Plugin thing", group: "My plugin", run() {} }, "plugin");
  const groups = shortcutDialogGroups(true);
  const names = groups.map((g) => g.context);
  assert.deepEqual(names.slice(0, 4), ["Global / viewer", "Panels", "Gallery walk", "Procedural cellbuilder"]);
  assert.ok(names.includes("My plugin"));
  const listed = groups.flatMap((g) => g.shortcuts.map((s) => s.description));
  for (const d of CORE_SHORTCUTS) assert.ok(listed.includes(d.label), d.id);
  assert.equal(listed.length, CORE_SHORTCUTS.length + 1, "each registry entry once, the plugin's included");
  const undo = groups[0].shortcuts.find((s) => s.description.startsWith("Undo"))!;
  assert.deepEqual(undo.keys, ["Ctrl / \u2318", "Z"]);
  assert.deepEqual(groups[0].shortcuts.find((s) => s.description.startsWith("Select the parent"))!.keys, ["Shift", "\u2191"]);
  // The panel-zone keys are listed only while they are live.
  assert.ok(!shortcutDialogGroups(false).some((g) => g.context === "Panels" && g.shortcuts.some((s) => s.keys[0] === "F6")));
});

test("a mounted viewer binds every dispatched core key and releases them on unmount", async () => {
  const { setupCameraControlsHandlers } = await import("@/components/viewer/sceneHelpers/setupCameraControlsHandlers");
  const { useGalleryStore } = await import("@/state/galleryStore");
  const { useMeStore } = await import("@/state/meStore");
  const { useViewerPanelStore } = await import("@/state/viewerPanelStore");
  const stub = {} as any;
  const off = setupCameraControlsHandlers(stub, stub, stub);
  assert.equal(listeners.size, 1);
  const live = (keys: string) => {
    const c = chordOf(keys);
    return listShortcuts().find((s) => !s.local && s.keys === keys)!.when!({ key: c.key, shiftKey: c.shiftKey, ctrlKey: c.ctrlKey, metaKey: false, altKey: false, target: { tagName: "DIV" } as any });
  };
  for (const d of dispatched) assert.equal(live(d.keys), true, `${d.id} is bound`);

  useGalleryStore.getState().setEnabled(false);
  const e = press("W", { shiftKey: true });
  assert.equal(useGalleryStore.getState().enabled, true, "Shift+W toggled gallery once");
  assert.equal(e.defaultPrevented, true);
  press("W", { shiftKey: true, target: { tagName: "INPUT" } });
  assert.equal(useGalleryStore.getState().enabled, true, "typing in a field leaves it");

  useMeStore.setState({ isAdmin: false });
  assert.equal(press("P", { shiftKey: true }).defaultPrevented, false, "not an admin: the key does nothing");
  assert.ok(!useViewerPanelStore.getState().shown.includes("admin"));
  useMeStore.setState({ isAdmin: true });
  assert.equal(press("P", { shiftKey: true }).defaultPrevented, true);
  assert.ok(useViewerPanelStore.getState().shown.includes("admin"));

  off();
  for (const d of dispatched) assert.equal(live(d.keys), false, `${d.id} is released`);
  assert.equal(listeners.size, 0);
});

test("Shift+Q and Shift+P open and close their panels through the panel intents", async () => {
  const { setupCameraControlsHandlers } = await import("@/components/viewer/sceneHelpers/setupCameraControlsHandlers");
  const { useMeStore } = await import("@/state/meStore");
  const { setPanelController } = await import("@/layout/reveal");
  // The layout's controller decides what opening means (a docked tab, a float brought forward):
  // the keys must ask it, not write the store flags behind its back.
  const open = new Set<string>();
  const calls: string[] = [];
  setPanelController({
    isOpen: (id) => open.has(id),
    reveal: (id) => (calls.push(`reveal ${id}`), open.add(id), true),
    hide: (id) => (calls.push(`hide ${id}`), open.delete(id), true),
  });
  const off = setupCameraControlsHandlers({} as any, {} as any, {} as any);
  try {
    useMeStore.setState({ isAdmin: true });
    press("Q", { shiftKey: true });
    press("Q", { shiftKey: true });
    press("P", { shiftKey: true });
    assert.deepEqual(calls, ["reveal core:options", "hide core:options", "reveal core:admin"]);
  } finally {
    off();
    setPanelController(null);
    useMeStore.setState({ isAdmin: false });
  }
});