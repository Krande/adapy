// A pop-out window's arrangement: its grid without a view, docking an arriving
// panel, the grid drawn with the window's own moves, and "Move to window".

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { registerPanel, getPanel } = await import("@/layout/registry");
const { emptyLayoutDoc, gridOf } = await import("@/layout/reducers");
const { shownGrid, gridGroups, groupOf } = await import("@/layout/splitTree");
const { dockAtWindowEdge, dockInWindow, removeFromWindow, windowContext, windowPanels } = await import("@/layout/windowLayout");
const { GridOpsProvider, GridView, CLASSIC_GRID_OPS } = await import("@/layout/presets/classicGrid");
const { createWindowBus, memoryTransportHub, MAIN_WINDOW_ID } = await import("@/layout/windowBus");
const { moveToWindowItem } = await import("@/layout/windows");

const body = () => Promise.resolve({ default: () => null });
registerPanel({ id: "w:a", title: "Alpha", body, content: body, capabilities: { popout: true } }, "test");
registerPanel({ id: "w:b", title: "Beta", body, content: body, capabilities: { popout: true } }, "test");
registerPanel({ id: "w:scene", title: "Scene thing", body, content: body, capabilities: { popout: true }, constraints: { requiresCanvas: true } }, "test");
registerPanel({ id: "w:mirror", title: "Mirrored", body, content: body, capabilities: { mirrored: true }, constraints: { requiresCanvas: true } }, "test");

const fresh = () => emptyLayoutDoc("classic", [], windowContext("w2"));

test("the first panel makes the window's first group; the next stack into the group last used", () => {
  let d = dockInWindow(fresh(), "w:a");
  assert.deepEqual(windowPanels(d), ["w:a"]);
  d = dockAtWindowEdge(d, "w:b", "right");
  assert.equal(gridGroups(gridOf(d)).length, 2);
  const gb = groupOf(gridOf(d), "w:b")!.id;
  d = dockInWindow(removeFromWindow(d, "w:a"), "w:a", gb);
  assert.equal(groupOf(gridOf(d), "w:a")!.id, gb, "stacked into the group last used");
  assert.equal(groupOf(gridOf(d), "w:a")!.active, "w:a");
  // Already here: brought forward where it is, not moved.
  const again = dockInWindow(d, "w:b");
  assert.equal(groupOf(gridOf(again), "w:b")!.id, gb);
  assert.equal(groupOf(gridOf(again), "w:b")!.active, "w:b");
  // A group that is gone: the first group.
  const into = dockInWindow(removeFromWindow(d, "w:a"), "w:a", "g99");
  assert.ok(groupOf(gridOf(into), "w:a"));
});

test("the window's grid is drawn without the view; empty, it is the viewport alone", () => {
  const d = dockAtWindowEdge(dockInWindow(fresh(), "w:a"), "w:b", "bottom");
  const shown = shownGrid(gridOf(d), () => true, { viewport: false });
  assert.equal(shown.kind, "split");
  const html = renderToStaticMarkup(
    React.createElement(
      GridOpsProvider,
      { ops: { ...CLASSIC_GRID_OPS, zone: "popout", panelActions: () => [] } },
      React.createElement(GridView, { grid: shown, doc: d, frame: { current: null }, onViewportRect() {} }),
    ),
  );
  assert.doesNotMatch(html, /data-grid-viewport/, "no view in a pop-out window");
  assert.equal((html.match(/data-grid-group=/g) ?? []).length, 2);
  assert.equal((html.match(/role="separator"/g) ?? []).length, 1);
  assert.equal(shownGrid(gridOf(fresh()), () => true, { viewport: false }).kind, "viewport");
});

test("who may go to a pop-out window: scene-free opt-ins, and mirrored panels", async () => {
  const { canPopOut } = await import("@/layout/popout");
  assert.equal(canPopOut(getPanel("w:a")!), true);
  assert.equal(canPopOut(getPanel("w:scene")!), false);
  assert.equal(canPopOut(getPanel("w:mirror")!), true);
});

test("Move to window lists the other windows (main first) and a new one; a move hands the panel over", async () => {
  const hub = memoryTransportHub();
  const main = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(), timers: false });
  const w2 = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), timers: false });
  const w3 = createWindowBus({ id: "w3", label: "Charts", transport: hub.connect(), timers: false });
  main.start();
  w2.start();
  w3.start();
  const item = moveToWindowItem(getPanel("w:a")!, { remove() {}, restore() {} }, w2)!;
  assert.equal(item.label, "Move to window");
  const items = typeof item.items === "function" ? item.items() : item.items!;
  assert.deepEqual(items.map((i) => i.label), ["Main", "Charts", "New window"]);
  // A panel that may not pop out: only back to the main window, from a pop-out; nothing from the main window.
  const scene = moveToWindowItem(getPanel("w:scene")!, { remove() {}, restore() {} }, w2)!;
  const sceneItems = typeof scene.items === "function" ? scene.items() : scene.items!;
  assert.deepEqual(sceneItems.map((i) => i.label), ["Main"]);
  assert.equal(moveToWindowItem(getPanel("w:scene")!, { remove() {}, restore() {} }, main), null);
  // The hand-over.
  const got: string[] = [];
  w3.on("dock-panel", (m) => {
    got.push(m.panelId);
    w3.post({ type: "dock-ack", to: m.from, moveId: m.moveId, ok: true });
  });
  let removed = 0;
  const move = moveToWindowItem(getPanel("w:a")!, { remove: () => removed++, restore() {} }, w2)!;
  (typeof move.items === "function" ? move.items() : move.items!).find((i) => i.label === "Charts")!.run();
  assert.deepEqual(got, ["w:a"]);
  assert.equal(removed, 1);
});
