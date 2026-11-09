// Core's tool rail: a shell's list as data (a component of its own still
// works), the user's list kept per shell in the layout prefs and brought up to
// date when the shell's list grows, the customise dialog's edits, and the rail
// and the dialog as drawn.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const R = await import("@/layout/toolRailModel");
const { ToolRail, ToolRailCustomiseDialog, ToolRailCustomiseHost } = await import("@/layout/ToolRail");
const RS = await import("@/layout/toolRailState");
const C = await import("@/layout/commands");
const { layoutStore, memoryLayoutPersistence, migrateLayoutPrefs } = await import("@/layout/layoutStore");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const D = await import("@/layout/desktop/desktopShell");
const { desktopMenus, menuCommandIds } = await import("@/layout/desktop/desktopMenus");

const S = R.RAIL_SEPARATOR;

test("chrome.rail as data, or a component of the shell's own (then not core's rail)", () => {
  assert.equal(R.railConfig(undefined), null);
  assert.equal(R.railConfig(() => Promise.resolve({ default: () => null })), null, "a lazy component: the shell's own rail");
  assert.deepEqual(R.railConfig({ tools: ["a", "", 3, S, "b"] }), { tools: ["a", S, "b"], customisable: true });
  assert.equal(R.railConfig({ tools: [], customisable: false })!.customisable, false);
  assert.equal(R.railConfig({ tools: "a" }), null);
});

test("a rail is tidied: no separator at either end or twice, a tool once, what is not offered left out", () => {
  assert.deepEqual(R.tidyRail([S, "a", S, S, "b", "a", S]), ["a", S, "b"]);
  assert.deepEqual(R.tidyRail(["a", S, "gone", S, "b"], (id) => id !== "gone"), ["a", S, "b"], "a group emptied takes its line");
  assert.deepEqual(R.tidyRail([S, "gone"], (id) => id !== "gone"), []);
});

test("the user's rail is theirs; a tool the shell adds later is put in beside its neighbour; one they removed stays out", () => {
  const v1 = ["open", S, "fit", "fit-sel", S, "hide", "show"];
  assert.deepEqual(R.railTools(v1, null), v1, "nothing saved: the shell's own");
  // They removed fit-sel, moved show above hide, and added undo.
  const saved = { tools: ["open", S, "fit", S, "show", "hide", "undo"], base: v1 };
  assert.deepEqual(R.railTools(v1, saved), saved.tools);
  // The shell ships v2: "iso" after fit-sel, "edges" at the end, "new-top" first; fit-sel is still removed.
  const v2 = ["new-top", "open", S, "fit", "fit-sel", "iso", S, "hide", "show", S, "edges"];
  // Each goes after the tool before it in the shell's list (iso after fit, its kept neighbour; edges after show).
  assert.deepEqual(R.railTools(v2, saved), ["new-top", "open", S, "fit", "iso", S, "show", "edges", "hide", "undo"]);
  // A tool the shell dropped is kept in their list (left out when drawn, while nothing offers it).
  assert.deepEqual(R.railTools(["open"], { tools: ["open", "old"], base: ["open", "old"] }), ["open", "old"]);
  assert.equal(R.railIsDefault(v1, null), true);
  assert.equal(R.railIsDefault(v1, { tools: [...v1], base: v1 }), true);
  assert.equal(R.railIsDefault(v1, saved), false);
});

test("the dialog's edits: move, add (never twice, separators as many as wanted), remove", () => {
  const list = ["a", "b", S, "c"];
  assert.deepEqual(R.moveRailTool(list, 0, 1), ["b", "a", S, "c"]);
  assert.deepEqual(R.moveRailTool(list, 3, 0), ["c", "a", "b", S]);
  assert.deepEqual(R.moveRailTool(list, 1, 99), ["a", S, "c", "b"], "clamped");
  assert.deepEqual(R.moveRailTool(list, 9, 0), list);
  assert.deepEqual(R.addRailTool(list, "d"), [...list, "d"]);
  assert.deepEqual(R.addRailTool(list, "d", 1), ["a", "d", "b", S, "c"]);
  assert.deepEqual(R.addRailTool(list, "a"), list, "already on the rail");
  assert.deepEqual(R.addRailTool(list, S, 1), ["a", S, "b", S, "c"]);
  assert.deepEqual(R.removeRailTool(list, 1), ["a", S, "c"]);
});

test("saved per shell in the layout prefs, through the persistence; equal to the shell's list it is not stored", async () => {
  const p = memoryLayoutPersistence();
  await layoutStore.getState().hydrate(p);
  try {
    const defaults = ["a", S, "b"];
    RS.saveRail("shell-x", defaults, ["b", "a", "c"]);
    assert.deepEqual(layoutStore.getState().prefs.rail, { "shell-x": { tools: ["b", "a", "c"], base: ["a", S, "b"] } });
    assert.deepEqual(RS.railList("shell-x", defaults), ["b", "a", "c"]);
    assert.deepEqual(RS.railList("shell-y", defaults), defaults, "another shell keeps its own");
    assert.deepEqual(p.saved?.rail?.["shell-x"]?.tools, ["b", "a", "c"], "handed to the persistence (the server, for a signed-in user)");
    RS.saveRail("shell-x", defaults, ["a", S, "b"]);
    assert.equal(layoutStore.getState().prefs.rail?.["shell-x"], undefined, "back to the shell's list: nothing stored");
    // Moving the first tool below the line after it: the line is kept where the user put it (the
    // rail as drawn leaves a line at the top out), and a tool listed twice is kept once.
    RS.saveRail("shell-x", defaults, R.moveRailTool(defaults, 0, 1).concat("b"));
    assert.deepEqual(RS.railList("shell-x", defaults), [S, "a", "b"]);
    assert.deepEqual(R.tidyRail(RS.railList("shell-x", defaults)), ["a", "b"]);
    RS.resetRail("shell-x");
    assert.deepEqual(RS.railList("shell-x", defaults), defaults);
  } finally {
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

test("saved prefs from another version: a broken rail or choice is dropped, a good one kept", () => {
  const prefs = migrateLayoutPrefs({
    v: 1,
    docs: {},
    rail: { good: { tools: ["a", 5, "b"], base: ["a"] }, bad: { tools: "a" }, worse: null },
    markingMenuByShell: { desktop: false, core: "yes" },
  });
  assert.deepEqual(prefs.rail, { good: { tools: ["a", "b"], base: ["a"] } });
  assert.deepEqual(prefs.markingMenuByShell, { desktop: false });
  assert.equal(R.migrateRailPrefs({ tools: [] })!.base.length, 0, "no base: every tool of the shell's counts as new");
  assert.equal(migrateLayoutPrefs({ v: 1, docs: {} }).rail, undefined);
});

test("the rail as drawn: the commands' icons, tooltips with the chord or the reason, pressed toggles, lines, the customise button", () => {
  C.registerCommand({ id: "t.fit", label: "Fit all", chordLabel: "Shift+A", run() {} }, "t");
  C.registerCommand({ id: "t.hide", label: "Hide selected", disabledReason: () => "Nothing selected", run() {} }, "t");
  C.registerCommand({ id: "t.edges", label: "Geometry edges", checked: () => true, run() {} }, "t");
  C.registerCommand({ id: "t.plain", label: "Plain thing", run() {} }, "t");
  try {
    const html = renderToStaticMarkup(
      React.createElement(ToolRail, { shell: "t-shell", config: { tools: [S, "t.fit", "missing", S, "t.hide", "t.edges", S, S, "t.plain", S] } }),
    );
    assert.match(html, /<nav[^>]*aria-label="Tools"/);
    assert.equal((html.match(/data-rail-tool=/g) ?? []).length, 4, "the offered tools; the missing one is left out");
    assert.equal((html.match(/role="separator"/g) ?? []).length, 2, "lines between groups only");
    assert.match(html, /aria-label="Fit all"[^>]*title="Fit all \(Shift\+A\)"/);
    assert.match(html, /aria-disabled="true"[^>]*title="Hide selected: Nothing selected"/);
    assert.match(html, /data-rail-tool="t.edges"[^>]*aria-pressed="true"/);
    assert.match(html, />PT</, "a command without an icon: its initials");
    assert.match(html, /aria-label="Customise tool rail"/);
    const fixed = renderToStaticMarkup(React.createElement(ToolRail, { shell: "t-shell", config: { tools: ["t.fit"], customisable: false } }));
    assert.doesNotMatch(fixed, /Customise tool rail/, "not customisable: no button");
  } finally {
    C.resetCommands();
  }
});

test("the customise dialog lists the rail's tools with their moves, and every other command to add", () => {
  C.registerCommand({ id: "t.fit", label: "Fit all", group: "View", run() {} }, "t");
  C.registerCommand({ id: "t.hide", label: "Hide selected", group: "Edit", run() {} }, "t");
  C.registerCommand({ id: "t.other", label: "Other thing", group: "Edit", run() {} }, "t");
  try {
    const html = renderToStaticMarkup(
      React.createElement(ToolRailCustomiseDialog, { shell: "t-shell", config: { tools: ["t.fit", S, "t.hide", "gone"] }, onClose() {} }),
    );
    assert.match(html, /role="dialog"[^>]*aria-label="Customise tool rail"/);
    assert.equal((html.match(/data-rail-row=/g) ?? []).length, 4, "tools and the separator");
    assert.match(html, /Separator/);
    assert.match(html, /\(not offered now\)/, "a tool nothing offers, kept but marked");
    assert.match(html, /aria-label="Move Fit all up"[^>]*disabled=""/, "the first cannot go up");
    assert.match(html, /aria-label="Remove Hide selected"/);
    assert.match(html, /data-rail-add="t.other"/, "a command not on the rail can be added");
    assert.doesNotMatch(html, /data-rail-add="t.fit"/, "one on it cannot");
    assert.match(html, /Reset to default<\/button>/);
    assert.match(html, /disabled=""[^>]*>Reset to default/, "nothing to reset");
    assert.equal(renderToStaticMarkup(React.createElement(ToolRailCustomiseHost, { shell: "t-shell", config: { tools: [] } })), "", "closed");
  } finally {
    C.resetCommands();
  }
});

test("the Desktop UI's rail names only core's commands, and its menus open the customise dialog", () => {
  const spec = D.desktopUiShellSpec();
  const cfg = R.railConfig(spec.chrome?.rail)!;
  assert.deepEqual(cfg.tools, D.DESKTOP_RAIL_TOOLS);
  registerCoreCommands();
  try {
    // A panel's command (`panel:<id>`) is core's too, listed only where the panel is offered (Storage: a server), with its own icon.
    const plain = cfg.tools.filter((id) => !R.isRailSeparator(id) && !id.startsWith("panel:"));
    for (const id of plain) assert.ok(C.getCommand(id), `${id} is a core command`);
    for (const id of plain) assert.ok(C.getCommand(id)!.icon, `${id} has an icon`);
    assert.deepEqual(R.tidyRail(cfg.tools), [...cfg.tools], "already tidy");
    const ids = menuCommandIds(desktopMenus({ modes: [], workspaces: [], shells: [], themes: [] }));
    for (const id of [ID.customiseRail, ID.markingMenu, ID.toggleMarkingMenu]) assert.ok(ids.includes(id), id);
  } finally {
    C.resetCommands();
  }
});
