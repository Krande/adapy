// The marking menu: the geometry (which sector a pointer is in), the right-button
// hold that opens it without taking the quick click (the Scene menu) or the drag
// (the pan) away, its items as the shell configures them, greyed in place, the
// wheel as drawn, and its key.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const MM = await import("@/layout/markingMenuModel");
const { MarkingMenuView, markingSlots, moreSlots, clampMarkingCentre, isViewTarget, MarkingMenuHost } = await import("@/layout/MarkingMenu");
const C = await import("@/layout/commands");
const S = await import("@/layout/shortcuts");
const { CORE_SHORTCUTS } = await import("@/layout/coreShortcutTable");
const { CORE_COMMAND_IDS: ID, cameraViewCommandId } = await import("@/layout/coreCommandIds");
const { layoutStore } = await import("@/layout/layoutStore");
const { servedMarkingMenu, setMarkingMenuOn } = await import("@/layout/markingMenuState");
const U = await import("@/plugins/uiShells");
const D = await import("@/layout/desktop/desktopShell");

const ctx = (over: Partial<import("@/layout/markingMenuModel").MarkingMenuContext> = {}) => ({ mode: null, feaResult: false, selectionCount: 0, ...over });

test("a pointer's sector: clockwise from north, screen y down, nothing in the dead zone", () => {
  assert.equal(MM.sectorAt(0, -60), 0, "up is north");
  assert.equal(MM.sectorAt(50, -50), 1, "north-east");
  assert.equal(MM.sectorAt(60, 0), 2, "east");
  assert.equal(MM.sectorAt(0, 60), 4, "south");
  assert.equal(MM.sectorAt(-60, 0), 6, "west");
  assert.equal(MM.sectorAt(-50, -50), 7, "north-west");
  assert.equal(MM.sectorAt(0, 0), null);
  assert.equal(MM.sectorAt(10, -10), null, "inside the 24 px dead zone");
  assert.equal(MM.sectorAt(0, -23.9), null);
  assert.equal(MM.sectorAt(0, -24), 0, "the dead zone's edge counts");
  assert.equal(MM.sectorAt(0, -10, 8, 5), 0, "a dead zone of the shell's own");
  // Sector edges sit half way between the items: 22.5 degrees either side of north.
  const at = (deg: number) => MM.sectorAt(Math.sin((deg * Math.PI) / 180) * 80, -Math.cos((deg * Math.PI) / 180) * 80);
  assert.equal(at(22), 0);
  assert.equal(at(23), 1);
  assert.equal(at(-22), 0);
  assert.equal(at(-23), 7);
  // Fewer items, wider sectors.
  assert.equal(MM.sectorAt(60, 0, 4), 1, "four items: east is the second");
  assert.equal(MM.sectorAt(-60, 10, 4), 3);
  assert.equal(MM.sectorAt(60, 0, 0), null);
  assert.equal(MM.directionOf(3), "SE");
  assert.deepEqual(MM.sectorOffset(0, 8, 100), { x: 0, y: -100 });
  assert.deepEqual(MM.sectorOffset(2, 8, 100), { x: 100, y: 0 });
  assert.deepEqual(MM.sectorOffset(4, 8, 100), { x: 0, y: 100 });
});

test("arrow keys step round the slots that hold something, wrapping", () => {
  const filled = [true, false, true, false, false, false, false, true];
  assert.equal(MM.stepSelection(filled, null, 1), 0);
  assert.equal(MM.stepSelection(filled, 0, 1), 2);
  assert.equal(MM.stepSelection(filled, 7, 1), 0);
  assert.equal(MM.stepSelection(filled, 0, -1), 7);
  assert.equal(MM.stepSelection(filled, null, -1), 7);
  assert.equal(MM.stepSelection([false, false], null, 1), null);
});

/** A gesture with a hand-driven clock. */
function gesture(opts: { holdMs?: number; moveTolerancePx?: number } = {}) {
  let pending: (() => void) | null = null;
  const opened: { x: number; y: number }[] = [];
  const g = new MM.MarkingGesture({
    holdMs: opts.holdMs ?? 300,
    moveTolerancePx: opts.moveTolerancePx ?? 5,
    open: (x, y) => opened.push({ x, y }),
    setTimer: (fn) => {
      pending = fn;
      return 1;
    },
    clearTimer: () => {
      pending = null;
    },
  });
  return {
    g,
    opened,
    elapse: () => {
      const fn = pending;
      pending = null;
      fn?.();
    },
    timerPending: () => pending !== null,
  };
}

const right = (x = 100, y = 100, eligible = true) => ({ button: 2, x, y, eligible });

test("a quick right click is still the Scene menu (Windows: the contextmenu comes on the release)", () => {
  const { g, opened, timerPending } = gesture();
  g.down(right());
  assert.equal(timerPending(), true, "armed");
  assert.deepEqual(g.up({ button: 2, x: 100, y: 100 }), { kind: "pass" });
  assert.equal(timerPending(), false, "the hold is forgotten");
  assert.equal(g.contextMenu(), "pass", "the scene's own contextmenu handler gets it");
  assert.equal(opened.length, 0);
});

test("a quick right click on macOS / Linux: the press-time contextmenu is held back, then handed on", () => {
  const { g, opened } = gesture();
  g.down(right());
  assert.equal(g.contextMenu(), "swallow", "held back while it could still be a hold or a pan");
  assert.deepEqual(g.up({ button: 2, x: 101, y: 100 }), { kind: "replay" }, "a click after all: replay it to the Scene menu");
  assert.equal(g.contextMenu(), "pass", "the replayed event goes through");
  assert.equal(opened.length, 0);
});

test("a right drag is still a pan: no menu, the contextmenu after it goes through (the scene ignores a moved click)", () => {
  const { g, opened, elapse, timerPending } = gesture();
  g.down(right());
  assert.equal(g.move(103, 102), "pass", "inside the tolerance");
  assert.equal(timerPending(), true);
  assert.equal(g.move(120, 100), "pass", "past it: a pan, and the camera keeps the moves");
  assert.equal(timerPending(), false);
  elapse();
  assert.equal(opened.length, 0);
  assert.deepEqual(g.up({ button: 2, x: 140, y: 100 }), { kind: "pass" });
  assert.equal(g.contextMenu(), "pass");
  // macOS / Linux: a press-time contextmenu held back is dropped by the pan (no replay).
  g.down(right());
  g.contextMenu();
  g.move(140, 100);
  assert.deepEqual(g.up({ button: 2, x: 140, y: 100 }), { kind: "pass" });
});

test("a right hold opens the menu; the flick is kept from the camera, and the Scene menu stays shut", () => {
  const { g, opened, elapse } = gesture();
  g.down(right(200, 150));
  elapse();
  assert.deepEqual(opened, [{ x: 200, y: 150 }], "opened at the press");
  assert.equal(g.phase, "held");
  assert.equal(g.move(200, 80), "swallow", "moves while held do not pan");
  assert.deepEqual(g.up({ button: 2, x: 200, y: 80 }), { kind: "release", x: 200, y: 80 });
  assert.equal(g.contextMenu(), "swallow", "the release's contextmenu (Windows) does not open the Scene menu");
  assert.equal(g.contextMenu(), "pass", "only that one");
  // macOS / Linux: the press-time one is swallowed and never replayed.
  g.down(right());
  assert.equal(g.contextMenu(), "swallow");
  elapse();
  assert.equal(g.up({ button: 2, x: 100, y: 100 }).kind, "release");
});

test("only a plain right press of a mouse on the view arms it", () => {
  const { g, opened, elapse, timerPending } = gesture();
  g.down({ button: 0, x: 0, y: 0, eligible: true });
  assert.equal(timerPending(), false, "left: orbit and pick");
  g.down(right(0, 0, false));
  assert.equal(timerPending(), false, "not eligible (Shift / Ctrl / Alt, touch, a HUD, the builder)");
  elapse();
  assert.equal(opened.length, 0);
  assert.equal(g.contextMenu(), "pass");
  // A left press mid-hold is ignored; a cancelled pointer forgets the press.
  g.down(right());
  g.reset();
  elapse();
  assert.equal(opened.length, 0);
});

test("core's wheel: eight fixed slots in every context, greyed in place; FEA isolation listed with a result", () => {
  const plain = MM.resolveMarkingMenu(undefined, ctx());
  assert.equal(plain.items.length, 8);
  const where = (items: import("@/layout/markingMenuModel").MarkingMenuItemDef[], id: string) => items.indexOf(id);
  for (const c of [ctx(), ctx({ feaResult: true }), ctx({ selectionCount: 3 }), ctx({ mode: "results" })]) {
    const r = MM.resolveMarkingMenu(undefined, c);
    assert.equal(where(r.items, ID.fitAll), 0, "fit all is north");
    assert.equal(where(r.items, ID.hideSelected), 6, "hide is west");
    assert.equal(where(r.items, ID.showAll), 7, "show all north-west, beside hide");
    assert.equal(r.items[4], MM.MARKING_VIEWS_SUBMENU, "the views south");
  }
  assert.deepEqual(plain.more, [ID.undo, ID.redo]);
  assert.deepEqual(MM.resolveMarkingMenu(undefined, ctx({ feaResult: true })).more, [ID.feaIsolateSelectionSets, ID.feaClearIsolation, ID.undo, ID.redo]);
  assert.ok(MM.MARKING_VIEWS_SUBMENU.items.includes(cameraViewCommandId("isometric")));
});

test("a shell's wheel: a list or a function of the context; past eight slots goes to the list; a throwing one falls back", () => {
  const ten = Array.from({ length: 10 }, (_, i) => `x:${i}`);
  const r = MM.resolveMarkingMenu({ items: ten, more: ["x:more"] }, ctx());
  assert.deepEqual(r.items, ten.slice(0, 8));
  assert.deepEqual(r.more, ["x:more", "x:8", "x:9"]);
  const byMode = MM.resolveMarkingMenu({ items: (c) => [c.mode === "results" ? "x:result" : "x:model"], more: [] }, ctx({ mode: "results" }));
  assert.deepEqual(byMode.items, ["x:result"]);
  const warn = console.warn;
  console.warn = () => {};
  try {
    const broken = MM.resolveMarkingMenu({ items: () => { throw new Error("boom"); } }, ctx());
    assert.equal(broken.items[0], ID.fitAll, "core's wheel instead");
  } finally {
    console.warn = warn;
  }
  assert.deepEqual(MM.markingGestureOptions({ holdMs: 200, deadZonePx: -1 }), { holdMs: 200, moveTolerancePx: 5, deadZonePx: 24 });
});

test("where it is offered: off by default in core's UI, on in a shell that asks, none otherwise", () => {
  assert.deepEqual(MM.markingMenuAvailability(undefined, false), { available: true, defaultOn: false, config: undefined });
  assert.deepEqual(MM.markingMenuAvailability({ markingMenu: false }, false), { available: false, defaultOn: false });
  assert.equal(MM.markingMenuAvailability({}, true).available, false, "a composed shell that does not name it may have gestures of its own");
  assert.deepEqual(MM.markingMenuAvailability({ markingMenu: {} }, true), { available: true, defaultOn: true, config: {} });
  assert.equal(MM.markingMenuAvailability({ markingMenu: false }, true).available, false);
  assert.ok(D.desktopUiShellSpec().chrome?.markingMenu, "the Desktop UI has it");
});

test("the user's choice is kept per UI shell, and the Desktop UI has it on until they say otherwise", () => {
  const w = window as unknown as { location: unknown };
  const keep = w.location;
  const serve = (ui: string) => {
    w.location = new URL(`http://localhost/?ui=${ui}`);
    U.resetUiShells();
    U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", order: 0, load: async () => ({ default: () => null }) }, U.CORE_UI_SHELL_ID);
    D.registerDesktopUiShell();
  };
  try {
    serve("desktop");
    assert.equal(U.activeUiShellId(), D.DESKTOP_UI_SHELL_ID);
    assert.deepEqual([servedMarkingMenu().available, servedMarkingMenu().on], [true, true]);
    setMarkingMenuOn(false);
    assert.equal(servedMarkingMenu().on, false);
    assert.deepEqual(layoutStore.getState().prefs.markingMenuByShell, { desktop: false });
    serve("core");
    assert.deepEqual([servedMarkingMenu().available, servedMarkingMenu().on], [true, false], "core's UI: offered, off");
    setMarkingMenuOn(true);
    assert.equal(servedMarkingMenu().on, true);
    assert.deepEqual(layoutStore.getState().prefs.markingMenuByShell, { desktop: false, core: true });
    layoutStore.getState().setMarkingMenu("core", null);
    assert.deepEqual(layoutStore.getState().prefs.markingMenuByShell, { desktop: false });
  } finally {
    w.location = keep;
    U.resetUiShells();
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

test("slots: a command greyed with its reason, ticked when checked, an unoffered id an empty slot, an empty submenu none", () => {
  C.registerCommand({ id: "t.fit", label: "Fit all", chordLabel: "Shift+A", run() {} }, "t");
  C.registerCommand({ id: "t.hide", label: "Hide selected", disabledReason: () => "Nothing selected", run() {} }, "t");
  C.registerCommand({ id: "t.edges", label: "Edges", checked: () => true, run() {} }, "t");
  try {
    const byId = new Map(C.listCommands().map((c) => [c.id, c]));
    const slots = markingSlots(["t.fit", null, "missing", "t.hide", { label: "Sub", items: ["t.edges"] }, { label: "Empty", items: ["missing"] }, "t.edges"], byId);
    assert.equal(slots.length, 7, "every slot keeps its place");
    assert.equal(slots[0]!.chord, "Shift+A");
    assert.equal(slots[1], null);
    assert.equal(slots[2], null);
    assert.equal(slots[3]!.disabledReason, "Nothing selected");
    assert.ok(slots[4]!.submenu);
    assert.equal(slots[5], null);
    assert.equal(slots[6]!.checked, true);
    const more = moreSlots(["t.hide", "missing"], byId);
    assert.deepEqual(more.map((s) => s.id), ["t.hide"]);

    const html = renderToStaticMarkup(
      React.createElement(MarkingMenuView, { x: 300, y: 300, slots, more, active: 0, caption: undefined }),
    );
    assert.match(html, /role="menu"[^>]*aria-label="Marking menu"/);
    assert.equal((html.match(/data-marking-slot=/g) ?? []).length, 4, "the filled slots are drawn");
    assert.match(html, /aria-disabled="true"[^>]*title="Hide selected: Nothing selected"/, "greyed, with the reason as its tooltip");
    assert.match(html, /title="Fit all \(Shift\+A\)"/);
    assert.match(html, /aria-haspopup="menu"/, "the submenu");
    assert.match(html, /✓/, "the checked toggle");
    assert.match(html, /data-marking-more="0"/, "the list under the wheel");
  } finally {
    C.resetCommands();
  }
});

test("the wheel stays inside the window, and only the view itself takes the gesture", () => {
  assert.deepEqual(clampMarkingCentre(5, 5, { width: 1000, height: 800 }, 96, 0), { x: 176, y: 120 });
  assert.deepEqual(clampMarkingCentre(500, 400, { width: 1000, height: 800 }, 96, 0), { x: 500, y: 400 });
  assert.equal(clampMarkingCentre(500, 790, { width: 1000, height: 800 }, 96, 52).y, 800 - (96 + 34 + 52 + 8));
  const el = (matches: Record<string, boolean>) => ({ closest: (sel: string) => (Object.entries(matches).some(([k, v]) => v && sel.includes(k)) ? {} : null) });
  assert.equal(isViewTarget(el({ "data-viewport-input": true }) as unknown as EventTarget), true);
  assert.equal(isViewTarget(el({ "data-viewport-input": true, "data-viewport-cover": true }) as unknown as EventTarget), false, "a mode's cover over the view");
  assert.equal(isViewTarget(el({}) as unknown as EventTarget), false, "a panel or a HUD");
  assert.equal(isViewTarget(null), false);
});

test("its key is core's Shift+E, reserved, listed, and a command shows it", async () => {
  const def = CORE_SHORTCUTS.find((d) => d.id === "core:marking-menu")!;
  assert.equal(def.keys, "Shift+E");
  assert.ok(!def.local);
  assert.ok(S.RESERVED_SHORTCUTS.includes("Shift+E"));
  const warn = console.warn;
  console.warn = () => {};
  try {
    assert.equal(S.registerShortcut({ id: "x:e", keys: "Shift+E", label: "", run() {} }, "plugin"), false, "a plugin may not take it");
  } finally {
    console.warn = warn;
  }
  const { coreCommandSpecs } = await import("@/layout/coreCommands");
  const spec = coreCommandSpecs().find((s) => s.id === ID.markingMenu)!;
  assert.equal(C.commandChord(spec), "Shift+E");
  assert.equal(spec.when?.(), false, "not offered while no menu is mounted and on");
  assert.ok(coreCommandSpecs().find((s) => s.id === ID.toggleMarkingMenu));
});

test("the host renders nothing while the served UI has the menu off", () => {
  assert.equal(renderToStaticMarkup(React.createElement(MarkingMenuHost)), "");
});
