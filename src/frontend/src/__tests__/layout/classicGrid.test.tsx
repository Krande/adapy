// The classic preset's split grid as drawn, its places and moves, and a panel's
// own window given actions by its host.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { registerPanel, getPanel, listPanels } = await import("@/layout/registry");
const { CORE_PANEL_IDS, CORE_PANEL_OWNER, registerCorePanels } = await import("@/layout/corePanels");
const { CLASSIC_ZONES } = await import("@/layout/layout");
const { emptyLayoutDoc, dockInGridDoc, floatPanel, gridOf } = await import("@/layout/reducers");
const { shownGrid, groupOf, VIEWPORT_NODE } = await import("@/layout/splitTree");
const { GridView, previewArea } = await import("@/layout/presets/classicGrid");
const P = await import("@/layout/presets/classicPlace");
const await_layoutStore = await import("@/layout/layoutStore");
const shellModesMod = await import("@/layout/shellModes");
const { WindowDockProvider, WindowFrame, WindowDockActions } = await import("@/components/ui/PanelFrame");
const { FloatingWindow } = await import("@/components/ui/FloatingWindow");

registerCorePanels();
/** `createElement` without prop checks, for elements whose children are passed positionally. */
const h = React.createElement as unknown as (type: unknown, props: unknown, ...children: unknown[]) => React.ReactElement;
const body = () => Promise.resolve({ default: () => null });
const open = { isOpen: () => true, setOpen() {}, subscribe: () => () => {} };
for (const id of ["t:a", "t:b", "t:c"]) {
  registerPanel(
    { id, title: `Panel ${id.slice(2).toUpperCase()}`, body, content: body, binding: open, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, order: 1 } } },
    "test",
  );
}
const contents = Object.fromEntries(["t:a", "t:b", "t:c"].map((id) => [id, () => React.createElement("p", null, `content of ${id}`)]));

function docWith() {
  let d = emptyLayoutDoc("classic", []);
  d = dockInGridDoc(d, "t:a", { node: VIEWPORT_NODE, edge: "right" });
  d = dockInGridDoc(d, "t:b", { node: groupOf(gridOf(d), "t:a")!.id, edge: "center" });
  d = dockInGridDoc(d, "t:c", { node: VIEWPORT_NODE, edge: "bottom" });
  return d;
}

function draw(doc = docWith(), shows: (id: string) => boolean = () => true): string {
  const frame = { current: null };
  return renderToStaticMarkup(
    React.createElement(GridView, { grid: shownGrid(gridOf(doc), shows), doc, contents, frame, onViewportRect() {} }),
  );
}

test("the grid draws its groups as tab strips, the shown tab's content visible, dividers between", () => {
  const html = draw();
  assert.match(html, /data-grid-root/);
  assert.equal((html.match(/data-grid-viewport/g) ?? []).length, 1, "one viewport slot");
  assert.equal((html.match(/data-grid-group=/g) ?? []).length, 2);
  assert.equal((html.match(/role="separator"/g) ?? []).length, 2, "a divider in each split");
  assert.match(html, /aria-orientation="vertical"/);
  assert.match(html, /aria-orientation="horizontal"/);
  // The stacked group: B is the shown tab, A's panel is hidden but mounted.
  assert.match(html, /role="tab"[^>]*id="grid-tab-t:b"[^>]*aria-selected="true"/);
  assert.match(html, /role="tab"[^>]*id="grid-tab-t:a"[^>]*aria-selected="false"/);
  assert.match(html, /id="grid-panel-t:a"[^>]*hidden=""/);
  assert.match(html, /content of t:a/);
  assert.match(html, /content of t:b/);
  // Header actions of the shown panel: float, move, close.
  assert.match(html, /data-panel-actions="t:b"/);
  assert.match(html, /aria-label="Float in a window"/);
  assert.match(html, /aria-label="Move panel"/);
  assert.match(html, /aria-label="Close panel"/);
  assert.doesNotMatch(html, /data-layout-drop=/, "no drop targets while nothing is dragged");
  assert.match(html, /data-layout-zone="dock"/, "groups are F6 zones");
});

test("a group whose panels are all closed takes no room", () => {
  const html = draw(docWith(), (id) => id !== "t:c");
  assert.equal((html.match(/data-grid-group=/g) ?? []).length, 1);
  assert.equal((html.match(/role="separator"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /content of t:c/);
});

test("the area a drop gives the panel, as a preview", () => {
  assert.deepEqual(previewArea("left", 0.3), { left: "0%", top: "0%", width: "30%", height: "100%" });
  assert.deepEqual(previewArea("bottom", 0.5), { left: "0%", top: "50%", width: "100%", height: "50%" });
  assert.deepEqual(previewArea("center", 1), { left: "0%", top: "0%", width: "100%", height: "100%" });
});

test("every core panel has a home, and every one with a home may dock", () => {
  const core = listPanels().filter((p) => p.owner === CORE_PANEL_OWNER);
  const homeless = core.filter((p) => P.homeOf(p) === null).map((p) => p.id);
  assert.deepEqual(homeless, [CORE_PANEL_IDS.feaTable], "the data table is the Simulation panel's section");
  for (const p of core) if (P.homeOf(p)) assert.ok(P.canDock(p), `${p.id} may dock`);
  assert.equal(P.homeOf(getPanel(CORE_PANEL_IDS.tree)!), "tree");
  assert.equal(P.homeOf(getPanel(CORE_PANEL_IDS.nodeEditor)!), "window");
  assert.equal(P.homeOf(getPanel(CORE_PANEL_IDS.admin)!), "window");
  assert.ok(P.canFloat(getPanel(CORE_PANEL_IDS.simulation)!), "Simulation floats in the layout's window");
  assert.ok(!P.canFloat(getPanel(CORE_PANEL_IDS.attributeSearch)!), "a panel at home in a window floats in that one");
});

test("a panel shows at home with the setting off, and a docked one below `wide`", () => {
  const d = floatPanel(docWith(), "t:x", { x: 0, y: 0, w: 1, h: 1 });
  assert.equal(P.shownPlace(d, "t:a", true, false), "grid");
  assert.equal(P.shownPlace(d, "t:a", true, true), "home");
  assert.equal(P.shownPlace(d, "t:a", false, false), "home");
  assert.equal(P.shownPlace(d, "t:x", true, true), "float", "a window stays one: it takes the screen there");
});

test("Move panel: float, the view's four edges, the other groups by their panels, back home; no zone names", () => {
  const doc = docWith();
  const labels = (id: string) => P.moveAction(getPanel(id)!, doc).menu!.map((i) => i.label);
  assert.deepEqual(labels("t:a"), [
    "Float in a window",
    "Dock left of the view",
    "Dock right of the view",
    "Dock top of the view",
    "Dock bottom of the view",
    "Stack with Panel C",
    "Back to the panel column",
  ]);
  assert.ok(labels("t:c").includes("Stack with Panel A, Panel B"));
  const atHome = P.moveAction(getPanel("t:a")!, emptyLayoutDoc("classic", [])).menu!;
  assert.equal(atHome.find((i) => i.id === "home")!.current, true);
  const search = P.moveAction(getPanel(CORE_PANEL_IDS.attributeSearch)!, doc).menu!.map((i) => i.label);
  assert.equal(search[0], "Float in a window");
  assert.ok(!search.some((l) => l.startsWith("Back to")), "its home is its own window: Float");
  for (const l of [...labels("t:a"), ...search]) assert.doesNotMatch(l, /inner|outer|aux/i);
});

test("a column panel's header offers Float and the Move menu (a panel that cannot go to a window has no pop-out entry)", async () => {
  const { columnActions } = await import("@/layout/presets/classicUndock");
  const ids = (id: string) => columnActions(getPanel(id)!, emptyLayoutDoc("classic", [])).map((a) => a.id);
  assert.deepEqual(ids(CORE_PANEL_IDS.componentControls), ["float", "move"]);
  assert.deepEqual(columnActions(getPanel(CORE_PANEL_IDS.componentControls)!).map((a) => a.id), ["float"], "no document: Float only");
  const move = columnActions(getPanel(CORE_PANEL_IDS.componentControls)!, emptyLayoutDoc("classic", [])).find((a) => a.id === "move")!;
  assert.ok(move.menu!.some((i) => i.label === "Dock right of the view"));
});

test("a drop on the panel column takes only a column panel; a grid drop needs an edge or a position", () => {
  assert.equal(P.dropPanel(CORE_PANEL_IDS.tree, { zone: CLASSIC_ZONES.menuColumn, index: null }), false);
  assert.equal(P.dropPanel("t:a", { zone: "grid:g1", index: null }), false);
  assert.equal(P.dropPanel("t:a", null), false);
  assert.equal(P.gridNodeOfZone(P.gridZone("g7")), "g7");
  assert.equal(P.gridNodeOfZone("classic.menu-column"), null);
});

test("a plugin panel that names a dock may dock and float, though its home is inside a core panel", () => {
  registerPanel(
    { id: "pl:sec", title: "Sec", body, content: body, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, parent: "core:scene", as: "section" }, docked: { zone: "bottom" } } },
    "pl",
  );
  registerPanel({ id: "pl:bare", title: "Bare", body, content: body, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, parent: "core:scene", as: "section" } } }, "pl");
  assert.equal(P.homeOf(getPanel("pl:sec")!), null);
  assert.ok(P.canDock(getPanel("pl:sec")!));
  assert.ok(P.canFloat(getPanel("pl:sec")!));
  assert.ok(!P.canDock(getPanel("pl:bare")!), "a section with no dock stays in its host");
  assert.equal(P.dockOf(getPanel("pl:sec")!), "bottom");
});

test("a composed shell places dock-naming plugin panels once: into the mapped group, else at that edge", () => {
  const { layoutStore } = await_layoutStore;
  registerPanel({ id: "pl:left", title: "L", body, content: body, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, order: 1 }, docked: { zone: "left" } } }, "pl");
  registerPanel({ id: "pl:right", title: "R", body, content: body, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, order: 1 }, docked: { zone: "right" } } }, "pl");
  const grid = { kind: "split", id: "s1", dir: "row", sizes: [0.7, 0.3], children: [{ kind: "viewport" }, { kind: "group", id: "gR", panels: [CORE_PANEL_IDS.scene], active: CORE_PANEL_IDS.scene }] } as const;
  P.setClassicShell({ id: "shell-t", grid: grid as never, docks: { right: "gR" } });
  try {
    assert.equal(P.classicContext(), "shell-t");
    P.adoptDockedPanels();
    let g = gridOf(P.classicDoc());
    assert.deepEqual(groupOf(g, "pl:right")?.panels, [CORE_PANEL_IDS.scene, "pl:right"], "stacked into the mapped group");
    assert.ok(groupOf(g, "pl:left"), "no group for `left`: docked at the view's left edge");
    assert.notEqual(groupOf(g, "pl:left")?.id, "gR");
    // Once only: the user's move stands.
    P.updateClassic((d) => floatPanel(d, "pl:right", { x: 0, y: 0, w: 10, h: 10 }));
    P.adoptDockedPanels();
    g = gridOf(P.classicDoc());
    assert.equal(groupOf(g, "pl:right"), null);
    // `pl:sec` (above) names `bottom`, which has no group: the view's bottom edge.
    assert.equal(groupOf(g, "pl:sec")?.panels.includes("pl:sec"), true);
    assert.deepEqual(P.placedElsewhere(P.classicDoc(), true, false).sort(), ["pl:left", "pl:right", "pl:sec"]);
    assert.deepEqual(P.placedElsewhere(P.classicDoc(), false, false), [], "with the setting off, everything is at home");
  } finally {
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

test("a shell's modes: a document and a starting grid each, and panels offered only in their modes", () => {
  const { layoutStore } = await_layoutStore;
  const M = shellModesMod;
  const viewOnly = { kind: "viewport" } as const;
  const withScene = { kind: "split", id: "s1", dir: "row", sizes: [0.7, 0.3], children: [{ kind: "viewport" }, { kind: "group", id: "g1", panels: [CORE_PANEL_IDS.scene], active: CORE_PANEL_IDS.scene }] } as const;
  M.setShellModes([
    { id: "inspect", label: "Inspect", order: 0 },
    { id: "results", label: "Results", order: 1 },
  ], "results");
  P.setClassicShell({ id: "sh", grid: viewOnly as never, modes: { results: { grid: withScene as never } } }, ["inspect", "results"]);
  try {
    assert.equal(M.activeShellMode(), "results", "the remembered mode");
    assert.equal(P.classicContext(), "sh/results");
    assert.ok(groupOf(gridOf(P.classicDoc()), CORE_PANEL_IDS.scene), "results starts from its own grid");
    M.setShellMode("inspect");
    assert.equal(P.classicContext(), "sh/inspect");
    assert.equal(gridOf(P.classicDoc()).kind, "viewport", "inspect starts from the shell's grid");
    // A move in one mode leaves the other mode's document alone.
    P.dockAt("t:a", { node: VIEWPORT_NODE, edge: "left" });
    assert.ok(groupOf(gridOf(P.classicDoc()), "t:a"));
    M.setShellMode("results");
    assert.equal(groupOf(gridOf(P.classicDoc()), "t:a"), null);
    // Leaving inspect: what it placed and results does not (t:a) closes; scene, placed by both, stays.
    P.dockAt(CORE_PANEL_IDS.scene, { node: VIEWPORT_NODE, edge: "right" }); // results' own grid already has it
    const hidden: string[] = [];
    P.leaveContext("sh/inspect", (id) => (hidden.push(id), true), () => true);
    assert.deepEqual(hidden, ["t:a"]);
    assert.equal(M.setShellMode("nope"), false, "an unknown mode is ignored");
    assert.equal(M.offeredInMode({ modes: ["inspect"] }), false);
    assert.equal(M.offeredInMode({ modes: ["results", "inspect"] }), true);
    assert.equal(M.offeredInMode({}), true, "a panel naming no modes shows in every one");
  } finally {
    M.setShellModes([]);
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
  assert.equal(P.classicContext(), "default");
  assert.equal(M.offeredInMode({ modes: ["inspect"] }), true, "core's own UI has no modes: everything is offered");
});

test("a panel's own window: the host's actions in its title bar, the zone marks on its card, nothing in its body", () => {
  const dock = {
    panelId: "t:a",
    actions: [{ id: "x", label: "Do it", run() {} }],
    onDragMove() {},
    onDragEnd: () => false,
  };
  const inner = React.createElement(WindowDockActions);
  const frame = React.createElement(WindowFrame, { title: "Win", actions: React.createElement("span", null, "own") }, inner);
  const html = renderToStaticMarkup(h(WindowDockProvider, { value: dock }, frame));
  assert.match(html, /data-layout-zone="float"/);
  assert.match(html, /data-layout-panel="t:a"/);
  assert.match(html, /data-panel-actions="t:a"[^>]*>.*aria-label="Do it".*own/);
  assert.equal((html.match(/Do it/g) ?? []).length, 3, "title, label and text of one button: none in the body");
  const bare = renderToStaticMarkup(React.createElement(WindowFrame, { title: "Win", actions: React.createElement("span", null, "own") }));
  assert.doesNotMatch(bare, /data-layout-zone|data-panel-actions/, "without a host: as before");
  // A window drawn inside the provider renders as it would without it.
  const win = (value: unknown) =>
    renderToStaticMarkup(
      h(WindowDockProvider, { value }, h(FloatingWindow, { dragHandleClassName: "h", zIndex: 1, initialRect: { x: 0, y: 0, width: 10, height: 10 } }, "x")),
    );
  assert.equal(win(dock), win(null));
});
