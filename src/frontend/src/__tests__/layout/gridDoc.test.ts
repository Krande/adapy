// The split grid in a layout document: a panel is in one place (its own, a
// window, or a group), the grid survives a save and a load, and an older
// document starts over. And the drop hit test's edges.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ZoneDef } from "@/layout/layout";
import { DROP_ATTR, DROP_EDGES_ATTR, dropTargetAt, edgeAt, endDockDragAt, moveDockDrag, dockDragStore } from "@/layout/dockDrag";
import {
  LAYOUT_DOC_VERSION,
  closePanel,
  dockInGridDoc,
  dockPanel,
  emptyLayoutDoc,
  floatPanel,
  floatPanelAt,
  gridOf,
  isInGrid,
  migrateLayoutDoc,
  minimisePanel,
  updateGrid,
  whereOpen,
} from "@/layout/reducers";
import { DEFAULT_GRID, VIEWPORT_NODE, groupOf, setActiveTab } from "@/layout/splitTree";

const ZONES: ZoneDef[] = [{ id: "col", kind: "stack", label: "Column" }];
const R = { x: 10, y: 20, w: 300, h: 200 };
const fresh = () => emptyLayoutDoc("p", ZONES);

test("a fresh document has the viewport alone as its grid", () => {
  assert.deepEqual(fresh().grid, DEFAULT_GRID);
  assert.deepEqual(gridOf({ ...fresh(), grid: undefined }), DEFAULT_GRID, "an absent grid is the default one");
});

test("docking into the grid takes a panel out of its window and the tray", () => {
  let d = minimisePanel(floatPanel(fresh(), "a", R), "a");
  d = dockInGridDoc(d, "a", { node: VIEWPORT_NODE, edge: "right" });
  assert.equal(d.floats.a, undefined);
  assert.deepEqual(d.minimised, []);
  assert.ok(isInGrid(d, "a"));
  assert.equal(whereOpen(d, "a"), "grid");
  assert.equal(dockInGridDoc(d, "a", { node: VIEWPORT_NODE, edge: "center" }), d, "a drop that moves nothing is no change");
});

test("floating, docking back and closing take a panel out of the grid", () => {
  const docked = dockInGridDoc(fresh(), "a", { node: VIEWPORT_NODE, edge: "left" });
  const floated = floatPanel(docked, "a", R);
  assert.ok(!isInGrid(floated, "a"));
  assert.deepEqual(floated.floats.a, { ...R, z: 1 });
  assert.deepEqual(floated.grid, DEFAULT_GRID);
  const home = dockPanel(docked, "a");
  assert.ok(!isInGrid(home, "a"));
  assert.deepEqual(home.floats, {});
  assert.ok(!isInGrid(closePanel(docked, "a"), "a"));
  assert.equal(dockPanel(fresh(), "a").grid, fresh().grid, "a panel at home stays");
});

test("floating at the pointer keeps a remembered size and moves to the drop", () => {
  let d = floatPanel(fresh(), "a", R);
  d = dockInGridDoc(d, "a", { node: VIEWPORT_NODE, edge: "left" });
  d = floatPanelAt(d, "a", { x: 500, y: 40, w: 1, h: 1 });
  assert.equal(d.floats.a.x, 500);
  assert.equal(d.floats.a.y, 40);
  assert.equal(d.floats.a.w, 1, "nothing remembered after docking: the given size");
  d = floatPanelAt(d, "a", { x: 7, y: 8, w: 999, h: 999 });
  assert.deepEqual([d.floats.a.x, d.floats.a.y, d.floats.a.w], [7, 8, 1], "a floating window keeps its size");
});

test("the grid survives a save and a load; a panel listed in both the grid and a window stays in the grid", () => {
  let d = dockInGridDoc(fresh(), "a", { node: VIEWPORT_NODE, edge: "right" });
  d = dockInGridDoc(d, "b", { node: groupOf(gridOf(d), "a")!.id, edge: "center" });
  d = updateGrid(d, (g) => setActiveTab(g, "a"));
  d = floatPanel(d, "c", R);
  const back = migrateLayoutDoc(JSON.parse(JSON.stringify(d)), "p", ZONES);
  assert.deepEqual(back.grid, d.grid);
  assert.deepEqual(back.floats, d.floats);
  const both = migrateLayoutDoc({ ...JSON.parse(JSON.stringify(d)), floats: { a: { ...R, z: 1 } } }, "p", ZONES);
  assert.equal(both.floats.a, undefined);
  assert.ok(isInGrid(both, "a"));
});

test("an older document starts over", () => {
  assert.equal(LAYOUT_DOC_VERSION, 2);
  const v1 = { v: 1, preset: "p", zones: {}, floats: { a: { ...R, z: 1 } }, minimised: [], known: ["a"] };
  const d = migrateLayoutDoc(v1, "p", ZONES);
  assert.deepEqual(d, fresh());
});

test("updating the grid with no change is no change", () => {
  const d = fresh();
  assert.equal(updateGrid(d, (g) => g), d);
});

// ---------------------------------------------------------------------------
// The drop hit test.
// ---------------------------------------------------------------------------

test("where in a node a pointer drops: the nearest edge, or the middle of a group", () => {
  assert.equal(edgeAt(0.05, 0.5, "all"), "left");
  assert.equal(edgeAt(0.95, 0.5, "all"), "right");
  assert.equal(edgeAt(0.5, 0.02, "all"), "top");
  assert.equal(edgeAt(0.5, 0.97, "all"), "bottom");
  assert.equal(edgeAt(0.5, 0.5, "all"), "center");
  assert.equal(edgeAt(0.25, 0.5, "all"), "left", "past the middle, a group still splits");
  assert.equal(edgeAt(0.1, 0.5, "edges"), "left");
  assert.equal(edgeAt(0.5, 0.9, "edges"), "bottom");
  assert.equal(edgeAt(0.5, 0.5, "edges"), null, "the viewport's middle is no target: a panel floats there");
  assert.equal(edgeAt(0.3, 0.6, "edges"), null);
});

function el(attrs: Record<string, string>, rect = { left: 0, top: 0, width: 100, height: 100 }) {
  const self = {
    getAttribute: (n: string) => attrs[n] ?? null,
    getBoundingClientRect: () => rect,
    closest: (sel: string) => (sel === `[${DROP_ATTR}]` && attrs[DROP_ATTR] ? self : null),
  };
  return self;
}

test("a grid node under the pointer is a drop target at its edge", () => {
  const group = el({ [DROP_ATTR]: "grid:g1", [DROP_EDGES_ATTR]: "all" }, { left: 100, top: 0, width: 200, height: 100 });
  assert.deepEqual(dropTargetAt([group], 110, 50), { zone: "grid:g1", index: null, edge: "left" });
  assert.deepEqual(dropTargetAt([group], 200, 50), { zone: "grid:g1", index: null, edge: "center" });
  const vp = el({ [DROP_ATTR]: "grid:viewport", [DROP_EDGES_ATTR]: "edges" });
  assert.deepEqual(dropTargetAt([vp], 50, 90), { zone: "grid:viewport", index: null, edge: "bottom" });
  assert.equal(dropTargetAt([vp], 50, 50), null, "the viewport's middle is no target");
  const flat = el({ [DROP_ATTR]: "grid:viewport", [DROP_EDGES_ATTR]: "edges" }, { left: 0, top: 0, width: 0, height: 0 });
  assert.equal(dropTargetAt([flat, group], 110, 50)?.zone, "grid:g1", "a target with no area is passed over");
});

test("the drag store follows the edge under the pointer", () => {
  const vp = el({ [DROP_ATTR]: "grid:viewport", [DROP_EDGES_ATTR]: "edges" });
  const hit = (x: number, y: number) => dropTargetAt([vp], x, y);
  moveDockDrag("a", 0, 0, hit);
  moveDockDrag("a", 95, 50, hit);
  assert.deepEqual(dockDragStore.getState(), { panel: "a", over: "grid:viewport", index: null, edge: "right" });
  assert.deepEqual(endDockDragAt(50, 2, hit), { zone: "grid:viewport", index: null, edge: "top" });
  assert.deepEqual(dockDragStore.getState(), { panel: null, over: null, index: null, edge: null });
});
