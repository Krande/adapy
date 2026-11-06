// The command palette is core's, in every UI: Ctrl+K opens it in core's own UI
// and in a composed shell, never while typing in a field; a shell may add a
// chord or turn it off; one palette across an in-place shell switch.
//
// Mounted for real with react-dom/client over a minimal DOM (fixtures/miniDom).

import assert from "node:assert/strict";
import { test } from "node:test";

const dom = await import("../fixtures/miniDom");
await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const U = await import("@/plugins/uiShells");
const { default: UiShellHost } = await import("@/plugins/UiShellHost");
const P = await import("@/layout/CommandPalette");
const C = await import("@/layout/commands");
const { RESERVED_SHORTCUTS, coreShortcutKeys } = await import("@/layout/shortcuts");
const { listShellModes } = await import("@/layout/shellModes");
const { coreCommandSpecs } = await import("@/layout/coreCommands");
const { CORE_COMMAND_IDS } = await import("@/layout/coreCommandIds");

const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  });

const palettes = () => dom.miniDocument.querySelectorAll('[aria-label="Command palette"]').filter((e) => e.getAttribute("role") === "dialog");

function shells() {
  U.resetUiShells();
  localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
  dom.setLocation("http://localhost/");
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", load: () => Promise.resolve({ default: () => null }) }, "core");
  U.registerUiShell({ id: "desk", label: "Desk", chrome: { commandPalette: { keys: "Ctrl+K" } } }, "core");
  U.registerUiShell({ id: "alt", label: "Alt", chrome: { commandPalette: { keys: "Shift+K" } } }, "alt-plugin");
  U.registerUiShell({ id: "off", label: "Off", chrome: { commandPalette: false } }, "off-plugin");
}

/** Core's root as the host mounts it, reduced to what this is about: the palette's host. */
const Root: React.FC<{ shellId: string }> = () => React.createElement(P.CorePaletteHost);

async function mount() {
  const el = dom.container();
  const root = createRoot(el as unknown as Element);
  await act(async () => root.render(React.createElement(UiShellHost, { coreRoot: () => Promise.resolve({ default: Root }) })));
  await flush();
  return root;
}

async function press(key: string, mods: { ctrl?: boolean; shift?: boolean } = {}, target?: unknown) {
  let free = true;
  await act(async () => {
    free = dom.pressKey(key, mods, target);
  });
  await flush();
  return free;
}

async function close() {
  await act(async () => P.closeCommandPalette());
  await flush();
}

test("Ctrl+K is core's own key, shown beside the palette's command", () => {
  assert.equal(coreShortcutKeys(P.COMMAND_PALETTE_SHORTCUT), "Ctrl+K");
  assert.ok(RESERVED_SHORTCUTS.includes("Ctrl+K"), "plugins are refused it");
  assert.deepEqual(P.paletteConfig(undefined), { on: true });
  assert.deepEqual(P.paletteConfig({ chrome: { commandPalette: { keys: "ctrl + k" } } }), { on: true }, "its own chord adds nothing");
  assert.deepEqual(P.paletteConfig({ chrome: { commandPalette: { keys: "Shift+K" } } }), { on: true, extraKeys: "Shift+K" });
  assert.deepEqual(P.paletteConfig({ chrome: { commandPalette: false } }), { on: false });
});

test("Ctrl+K opens the palette in core's own UI and in a composed shell, never while typing; one palette across switches", async () => {
  shells();
  const root = await mount();
  try {
    assert.equal(U.activeUiShellId(), "core");
    assert.ok(C.getCommand(P.COMMAND_PALETTE_COMMAND), "the palette's command is registered in core's UI");
    assert.equal(C.commandChord(C.getCommand(P.COMMAND_PALETTE_COMMAND)!), "Ctrl+K");
    const field = dom.miniDocument.createElement("input");
    assert.equal(await press("k", { ctrl: true }, field), true, "typed into a field: the field's");
    assert.equal(palettes().length, 0);
    assert.equal(await press("k", { ctrl: true }), false, "handled, and the browser's own Ctrl+K prevented");
    assert.equal(palettes().length, 1, "open in core's UI");
    await close();

    await act(async () => U.setActiveUiShell("desk"));
    await flush();
    await press("k", { ctrl: true });
    assert.equal(palettes().length, 1, "open in the composed shell, and only one");
    await close();

    await act(async () => U.setActiveUiShell("alt"));
    await flush();
    await press("K", { shift: true });
    assert.equal(palettes().length, 1, "the shell's own chord opens it too");
    await close();
    await press("k", { ctrl: true });
    assert.equal(palettes().length, 1, "and Ctrl+K still does");
    await close();

    await act(async () => U.setActiveUiShell("off"));
    await flush();
    assert.equal(C.getCommand(P.COMMAND_PALETTE_COMMAND), undefined, "a shell that turns it off has no palette command");
    assert.equal(await press("k", { ctrl: true }), true, "and Ctrl+K is left to the browser");
    assert.equal(palettes().length, 0);
    await act(async () => P.openCommandPalette());
    await flush();
    assert.equal(palettes().length, 0, "nor does anything draw it");
    await close();

    await act(async () => U.setActiveUiShell("core"));
    await flush();
    await press("K", { shift: true });
    assert.equal(palettes().length, 0, "the other shell's extra chord left with it");
    await press("k", { ctrl: true });
    assert.equal(palettes().length, 1, "back in core's UI: on again");
    await close();
  } finally {
    await act(async () => root.unmount());
    localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
    U.resetUiShells();
  }
  assert.equal(C.getCommand(P.COMMAND_PALETTE_COMMAND), undefined, "unmounted: gone");
});

test("a command that only means something with modes says why it cannot run in core's own UI", () => {
  assert.equal(listShellModes().length, 0);
  const spec = coreCommandSpecs().find((s) => s.id === CORE_COMMAND_IDS.resetAllLayouts)!;
  assert.equal(spec.disabledReason?.(), "This user interface has no modes");
});
