// The docked preset's rules: its zones, where core's panels dock, which tab a
// zone shows, the grid around the viewport, and adoption of new panels.

import assert from "node:assert/strict";
import { test } from "node:test";

// The store modules read `window` at import time; give them a minimal realm.
const g = globalThis as Record<string, unknown>;
const cells = new Map<string, string>();
const storage = {
  getItem: (k: string) => (cells.has(k) ? cells.get(k)! : null),
  setItem: (k: string, v: string) => void cells.set(k, String(v)),
  removeItem: (k: string) => void cells.delete(k),
  clear: () => cells.clear(),
  key: () => null,
  length: 0,
};
g.window ??= {
  location: new URL("http://localhost/"),
  localStorage: storage,
  sessionStorage: storage,
  devicePixelRatio: 1,
  addEventListener() {},
  removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
g.localStorage ??= storage;
g.sessionStorage ??= storage;
g.document ??= {
  documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {} } },
  createElement: () => ({ style: {}, getContext: () => null }),
  addEventListener() {},
  removeEventListener() {},
};

const { CORE_PANEL_IDS, CORE_PANEL_OWNER, registerCorePanels } = await import("@/layout/corePanels");
const { listPanels } = await import("@/layout/registry");
const { DOCKED_PRESET_ID, DOCKED_ZONES } = await import("@/layout/layout");
const { emptyLayoutDoc, migrateLayoutDoc, movePanel } = await import("@/layout/reducers");
const R = await import("@/layout/presets/dockRules");

registerCorePanels();
const core = () => listPanels().filter((p) => p.owner === CORE_PANEL_OWNER);
const fresh = () => emptyLayoutDoc(DOCKED_PRESET_ID, R.DOCKED_ZONE_DEFS);
const ids = (ps: { id: string }[]) => ps.map((p) => p.id);

test("the docked preset has the six docks, each with limits and an arrangement, and its float zones", () => {
  assert.deepEqual(
    R.DOCK_ZONE_DEFS.map((z) => z.id),
    ["left", "left-aux", "right-aux2", "right-aux", "right", "bottom"],
  );
  for (const z of R.DOCK_ZONE_DEFS) {
    assert.ok(z.limits && z.limits.min <= z.limits.default && z.limits.default <= z.limits.max, z.id);
    assert.ok(z.arrangement, z.id);
    assert.ok(z.edge, z.id);
  }
  assert.ok(R.DOCKED_ZONE_DEFS.some((z) => z.id === DOCKED_ZONES.float && z.kind === "float"));
  assert.ok(R.DOCKED_ZONE_DEFS.some((z) => z.id === DOCKED_ZONES.window && z.kind === "float"));
});

test("every core panel has a docked placement in a zone the preset has", () => {
  const zones = new Set(R.DOCKED_ZONE_DEFS.map((z) => z.id));
  for (const p of core()) {
    const pl = p.placement?.[DOCKED_PRESET_ID];
    assert.ok(pl, `${p.id} has no docked placement`);
    assert.ok(zones.has(pl!.zone), `${p.id} docks in an unknown zone ${pl!.zone}`);
  }
});

test("the default docks: tree left, storage left-aux, object and scene info right, simulation bottom", () => {
  const d = fresh();
  const all = listPanels();
  assert.deepEqual(ids(R.dockZonePanels(d, "left", all)), [CORE_PANEL_IDS.tree]);
  assert.deepEqual(ids(R.dockZonePanels(d, "left-aux", all)), [CORE_PANEL_IDS.storage, CORE_PANEL_IDS.serverInfo]);
  assert.deepEqual(ids(R.dockZonePanels(d, "right", all)), [CORE_PANEL_IDS.objectInfo, CORE_PANEL_IDS.scene]);
  assert.deepEqual(ids(R.dockZonePanels(d, "right-aux", all)), [CORE_PANEL_IDS.options, CORE_PANEL_IDS.wsStatus]);
  assert.deepEqual(ids(R.dockZonePanels(d, "bottom", all)), [CORE_PANEL_IDS.simulation], "the data table is the simulation panel's");
  assert.ok(ids(R.dockZonePanels(d, "right-aux2", all)).includes(CORE_PANEL_IDS.cellbuilder));
  for (const id of [CORE_PANEL_IDS.nodeEditor, CORE_PANEL_IDS.attributeSearch, CORE_PANEL_IDS.admin, CORE_PANEL_IDS.convert]) {
    for (const z of R.DOCK_ZONE_DEFS) assert.ok(!ids(R.dockZonePanels(d, z.id, all)).includes(id), `${id} floats`);
  }
});

test("the document's placement wins over the default, in the document's order", () => {
  const all = listPanels();
  let d = movePanel(fresh(), CORE_PANEL_IDS.scene, "left", 0);
  assert.deepEqual(ids(R.dockZonePanels(d, "left", all)), [CORE_PANEL_IDS.scene, CORE_PANEL_IDS.tree]);
  assert.deepEqual(ids(R.dockZonePanels(d, "right", all)), [CORE_PANEL_IDS.objectInfo]);
  d = movePanel(d, CORE_PANEL_IDS.tree, "left", 0);
  assert.deepEqual(ids(R.dockZonePanels(d, "left", all)), [CORE_PANEL_IDS.tree, CORE_PANEL_IDS.scene]);
});

test("a zone shows its active tab while it is open, else the first open one", () => {
  const s = { panels: [], active: "b" };
  assert.equal(R.shownTab(s, ["a", "b"]), "b");
  assert.equal(R.shownTab(s, ["a", "c"]), "a");
  assert.equal(R.shownTab(undefined, []), null);
  const d = R.activateInZone(fresh(), "right", "y", ["x", "y"]);
  assert.equal(d.zones.right.active, "y");
  assert.deepEqual(d.zones.right.panels, ["x", "y"], "the zone's order is written down with it");
  assert.equal(R.activateInZone(d, "right", "y"), d);
  assert.equal(R.activateInZone(d, "right", "x").zones.right.panels, d.zones.right.panels);
  const back = migrateLayoutDoc(JSON.parse(JSON.stringify(d)), DOCKED_PRESET_ID, R.DOCKED_ZONE_DEFS);
  assert.equal(back.zones.right.active, "y", "and survives a save and a load");
});

test("a zone's track: nothing when empty, the strip when collapsed, else its size within limits", () => {
  const def = R.DOCK_ZONE_DEFS.find((z) => z.id === "right")!;
  assert.equal(R.trackSize(def, { panels: [], active: null }, false), 0);
  assert.equal(R.trackSize(def, { panels: [], active: null, collapsed: true }, true), R.COLLAPSED_STRIP);
  assert.equal(R.trackSize(def, { panels: [], active: null }, true), def.limits!.default);
  assert.equal(R.trackSize(def, { panels: [], active: null, size: 99999 }, true), def.limits!.max);
  assert.equal(R.trackSize(def, { panels: [], active: null, size: 1 }, true), def.limits!.min);
});

test("the grid keeps the viewport in one cell, the side docks full height and the bottom under the viewport", () => {
  const grid = R.dockGrid({ left: 300, right: 360, bottom: 200 });
  assert.equal(grid.gridTemplateColumns, "300px 0px minmax(0, 1fr) 0px 0px 360px");
  assert.equal(grid.gridTemplateRows, "auto minmax(0, 1fr) 200px");
  const rows = grid.gridTemplateAreas.match(/"[^"]+"/g)!.map((r) => r.slice(1, -1).split(" "));
  assert.equal(rows.length, 3);
  assert.ok(rows[0].every((c) => c === R.TOOLBAR_AREA));
  assert.equal(rows[1].filter((c) => c === R.VIEWPORT_AREA).length, 1);
  assert.equal(rows[1][0], R.zoneArea("left"));
  assert.equal(rows[2][0], R.zoneArea("left"), "the left dock is full height");
  assert.equal(rows[2][2], R.zoneArea("bottom"), "the bottom dock is under the viewport");
  assert.equal(rows[2][5], R.zoneArea("right"));
});

test("a plugin's top panel without a docked placement stacks in the inner right dock", () => {
  const pl = R.dockedPlacement({ placement: { classic: { zone: "classic.menu-column", order: 1005 } } });
  assert.deepEqual(pl, { zone: "right-aux", order: 1005 });
  assert.equal(R.dockedPlacement({ placement: { classic: { zone: "classic.menu-column", parent: "core:scene" } } }), undefined);
  assert.deepEqual(R.dockedPlacement({ placement: { docked: { zone: "bottom" } } }), { zone: "bottom" });
});

test("new unbound panels are opened in their dock once; bound panels are left to their flags", () => {
  const unbound = { ...core()[0], id: "p:a", binding: undefined, placement: { docked: { zone: "bottom", order: 5 } } };
  const bound = { ...core()[0], id: "p:b", placement: { docked: { zone: "bottom", order: 1 } } };
  let d = R.adoptDocked(fresh(), [unbound, bound]);
  assert.deepEqual(d.zones.bottom.panels, ["p:a"]);
  assert.deepEqual(d.known, ["p:a", "p:b"]);
  d = { ...d, zones: { ...d.zones, bottom: { ...d.zones.bottom, panels: [] } } };
  assert.deepEqual(R.adoptDocked(d, [unbound, bound]).zones.bottom.panels, [], "the user's close stands");
  const defaults = R.dockedDefaults({ context: "default", panels: core() });
  assert.equal(defaults.known.length, core().length);
  for (const z of R.DOCK_ZONE_DEFS) assert.deepEqual(defaults.zones[z.id].panels, [], "core's panels are all bound");
});
