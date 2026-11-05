// What a docked UI needs of the split grid: groups held at a px size, collapsed
// to a strip of icons, stacked rather than tabbed; a shell's group actions; pins
// that cross modes; and a shell's changes to panels it does not own.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { registerPanel, getPanel, listPanels, setPanelOverrides } = await import("@/layout/registry");
const { CLASSIC_ZONES } = await import("@/layout/layout");
const { emptyLayoutDoc, gridOf } = await import("@/layout/reducers");
const T = await import("@/layout/splitTree");
const { GridView } = await import("@/layout/presets/classicGrid");
const P = await import("@/layout/presets/classicPlace");
const M = await import("@/layout/shellModes");
const { layoutStore } = await import("@/layout/layoutStore");

const body = () => Promise.resolve({ default: () => null });
const Icon = () => React.createElement("svg", { "data-icon": "" });
const open = { isOpen: () => true, setOpen() {}, subscribe: () => () => {} };
for (const id of ["d:a", "d:b", "d:c"]) {
  registerPanel(
    { id, title: `Dock ${id.slice(2)}`, icon: Icon, body, content: body, binding: open, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, order: 1 } } },
    "test",
  );
}
const contents = Object.fromEntries(["d:a", "d:b", "d:c"].map((id) => [id, () => React.createElement("p", null, `content of ${id}`)]));

const grid = (left: Partial<import("@/layout/splitTree").GroupNode>): import("@/layout/splitTree").GridNode => ({
  kind: "split",
  id: "s1",
  dir: "row",
  sizes: [0.3, 0.7],
  children: [{ kind: "group", id: "gL", panels: ["d:a", "d:b"], active: "d:a", ...left }, { kind: "viewport" }],
});

function draw(g: import("@/layout/splitTree").GridNode): string {
  const doc = { ...emptyLayoutDoc("classic", []), grid: g };
  return renderToStaticMarkup(React.createElement(GridView, { grid: T.shownGrid(g, () => true), doc, contents, frame: { current: null }, onViewportRect() {} }));
}

test("a fixed group keeps its px size, bounded, through the reducers and a stored round trip", () => {
  let g = grid({ fixed: { px: 280, min: 200, max: 400 } });
  assert.match(draw(g), /flex:0 0 280px/);
  g = T.setFixedPx(g, "gL", 900);
  assert.equal((T.findNode(g, "gL") as import("@/layout/splitTree").GroupNode).fixed!.px, 400, "clamped to max");
  g = T.setFixedPx(g, "gL", 10);
  assert.equal((T.findNode(g, "gL") as import("@/layout/splitTree").GroupNode).fixed!.px, 200, "clamped to min");
  const back = T.normaliseGrid(JSON.parse(JSON.stringify(g)));
  assert.deepEqual((T.findNode(back, "gL") as import("@/layout/splitTree").GroupNode).fixed, { px: 200, min: 200, max: 400 });
  assert.equal(T.normaliseGrid({ ...grid({}), children: [{ kind: "group", id: "g", panels: ["x"], active: "x", fixed: { px: -3 } }, { kind: "viewport" }] }).kind, "split");
});

test("docking beside a fixed group hands its size to the split that takes its place", () => {
  const g = T.dockInGrid(grid({ fixed: { px: 300 } }), "d:c", { node: "gL", edge: "bottom" });
  const wrapper = (g as import("@/layout/splitTree").SplitNode).children[0] as import("@/layout/splitTree").SplitNode;
  assert.equal(wrapper.kind, "split");
  assert.equal(wrapper.dir, "col");
  assert.deepEqual(wrapper.fixed, { px: 300 });
  assert.equal((wrapper.children[0] as import("@/layout/splitTree").GroupNode).fixed, undefined);
});

test("a collapsed group is a strip of its panels' icons that restores it; its neighbour cannot be dragged into it", () => {
  const g = T.setGroupCollapsed(grid({}), "gL", true);
  const html = draw(g);
  assert.match(html, new RegExp(`flex:0 0 ${T.COLLAPSED_PX}px`));
  assert.match(html, /aria-label="Expand Dock a"/);
  assert.match(html, /aria-label="Expand Dock b"/);
  assert.match(html, /data-icon/);
  assert.doesNotMatch(html, /role="separator"/, "a plain rule, not a divider, beside a strip");
  assert.doesNotMatch(html, /content of d:a/);
  assert.equal(T.setGroupCollapsed(g, "gL", true), g, "no change, same tree");
  assert.equal((T.findNode(T.setGroupCollapsed(g, "gL", false), "gL") as import("@/layout/splitTree").GroupNode).collapsed, undefined);
});

test("a stacked group shows every panel under its own header", () => {
  const html = draw(T.setGroupArrangement(grid({}), "gL", "stack"));
  assert.doesNotMatch(html, /role="tablist"/);
  assert.match(html, /content of d:a/);
  assert.match(html, /content of d:b/);
  assert.match(html, /aria-label="Close Dock a"/);
  assert.match(html, /aria-label="Close Dock b"/);
  // "auto" with no measured room: tabs.
  assert.match(draw(T.setGroupArrangement(grid({}), "gL", "auto")), /role="tablist"/);
});

test("a shell's group chrome: tab icons, add, collapse and pin", () => {
  P.setClassicShell({ id: "dock-shell", groups: { actions: ["add", "collapse", "pin"], tabIcons: true } });
  try {
    const html = draw(grid({}));
    assert.match(html, /role="tab"[^>]*>.*data-icon/, "the tab carries the panel's icon");
    assert.match(html, /aria-label="Add a panel here"/);
    assert.match(html, /aria-label="Collapse to a strip"/);
    assert.match(html, /aria-label="Pin: keep across modes"[^>]*aria-pressed="false"|aria-pressed="false"[^>]*aria-label="Pin: keep across modes"/);
    P.togglePin("d:a");
    assert.deepEqual(P.pinnedPanels(), ["d:a"]);
    P.togglePin("d:a");
    assert.deepEqual(P.pinnedPanels(), []);
  } finally {
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
  assert.doesNotMatch(draw(grid({})), /Add a panel here|Collapse to a strip|Pin:/, "core's own UI: none of them");
});

test("a pinned panel goes along to a mode that does not place it", () => {
  M.setShellModes([{ id: "one", label: "One", order: 0 }, { id: "two", label: "Two", order: 1 }], "one");
  P.setClassicShell({ id: "pin-shell", modes: { one: { grid: grid({}) } } }, ["one", "two"]);
  try {
    layoutStore.getState().setPinned("pin-shell", "d:b", true);
    M.setShellMode("two");
    const hidden: string[] = [];
    P.leaveContext("pin-shell/one", (id) => (hidden.push(id), true), () => true);
    assert.deepEqual(hidden, ["d:a"], "the unpinned panel leaves with its mode");
    assert.ok(T.groupOf(gridOf(P.classicDoc()), "d:b"), "the pinned one is placed in the new mode");
  } finally {
    M.setShellModes([]);
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

test("a shell changes a panel it does not own; options may follow the mode", () => {
  setPanelOverrides({
    "d:c": {
      title: "Renamed",
      modes: ["two"],
      capabilities: { fill: true, popout: true },
      options: (mode) => ({ tabs: mode === "two" ? ["b"] : ["a", "b"] }),
    },
  });
  try {
    const p = getPanel("d:c")!;
    assert.equal(p.title, "Renamed");
    assert.deepEqual(p.modes, ["two"]);
    assert.equal(p.capabilities.fill, true);
    assert.equal(p.capabilities.closable, true, "the rest of its capabilities stay");
    assert.equal(p.binding, open, "its binding stays");
    assert.deepEqual(p.options, { tabs: ["a", "b"] });
    assert.equal(getPanel("d:c"), p, "the same view until something changes");
    M.setShellModes([{ id: "two", label: "Two", order: 0 }]);
    assert.deepEqual(getPanel("d:c")!.options, { tabs: ["b"] });
    assert.equal(listPanels().find((x) => x.id === "d:c")!.title, "Renamed");
  } finally {
    M.setShellModes([]);
    setPanelOverrides(null);
  }
  assert.equal(getPanel("d:c")!.title, "Dock c");
});

test("a mode's badge: a count or a dot, cleared by null or zero", () => {
  M.setShellModes([{ id: "m", label: "M", order: 0 }]);
  try {
    M.setShellModeBadge("m", 3);
    assert.equal(M.shellModeBadge("m"), 3);
    M.setShellModeBadge("m", "dot");
    assert.equal(M.shellModeBadge("m"), "dot");
    M.setShellModeBadge("m", 0);
    assert.equal(M.shellModeBadge("m"), null);
  } finally {
    M.setShellModes([]);
  }
});
