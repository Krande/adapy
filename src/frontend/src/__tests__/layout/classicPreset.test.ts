// The classic preset's rules against the classic UI they reproduce (`Menu.tsx`,
// `app.tsx`), and the module-graph rule that keeps the catalogue from splitting
// the classic shell's chunk.
//
// The preset's DOM is compared with the hand-written tree's in a browser (the
// screenshot + DOM snapshot harness); these are the parts that can be held to
// the source without one.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
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
const { getPanel, listPanels, listToolbarPanels } = await import("@/layout/registry");
const { placementFor } = await import("@/layout/panel");
const { CLASSIC_PRESET_ID, CLASSIC_ZONES } = await import("@/layout/layout");
const { CLASSIC_TOOLBAR, CLASSIC_ZONE_DEFS, classicDefaults, classicMounts, toolbarPresence } = await import(
  "@/layout/presets/classicRules"
);

registerCorePanels();

const here = dirname(fileURLToPath(import.meta.url));
const src = (p: string) => readFileSync(resolve(here, "../..", p), "utf8");
const corePanels = () => listPanels().filter((p) => p.owner === CORE_PANEL_OWNER);
const S = (open: boolean, available: boolean) => ({ open, available });

test("every core toolbar panel has a classic toggle recipe, and every recipe a panel", () => {
  const toolbar = listToolbarPanels().filter((p) => p.owner === CORE_PANEL_OWNER).map((p) => p.id);
  assert.deepEqual([...toolbar].sort(), Object.keys(CLASSIC_TOOLBAR).sort());
});

test("the recipes' tooltips and accessible names are the ones Menu.tsx writes", () => {
  const menu = src("components/Menu.tsx");
  const literal = new Set<string>();
  for (const m of menu.matchAll(/(?:title|aria-label)="([^"]+)"/g)) literal.add(m[1]);
  for (const m of menu.matchAll(/"((?:Show|Hide) selection tree[^"]*)"/g)) literal.add(m[1]);
  for (const [id, r] of Object.entries(CLASSIC_TOOLBAR)) {
    if (r.statusMenu) continue; // the connection status draws its own button
    for (const open of [false, true]) {
      const title = r.title(open);
      if (id === CORE_PANEL_IDS.attributeSearch) assert.match(menu, /title=\{SEARCH_WINDOW_TITLE\}/);
      else assert.ok(literal.has(title), `${id}: title "${title}" is not in Menu.tsx`);
      const aria = r.ariaLabel?.(open);
      if (aria && id !== CORE_PANEL_IDS.attributeSearch) assert.ok(literal.has(aria), `${id}: aria-label "${aria}"`);
    }
  }
});

test("a toggle is left out, hidden or shown as in Menu.tsx", () => {
  const rest = { restMode: true, websocketDeactivated: false };
  const desktop = { restMode: false, websocketDeactivated: false };
  const p = (id: string) => getPanel(id)!;
  // Hosted-only buttons are absent on the desktop viewer, present (not hidden) when hosted.
  assert.equal(toolbarPresence(p(CORE_PANEL_IDS.storage), S(false, false), desktop), "omit");
  assert.equal(toolbarPresence(p(CORE_PANEL_IDS.storage), S(false, true), rest), "shown");
  assert.equal(toolbarPresence(p(CORE_PANEL_IDS.attributeSearch), S(false, false), rest), "hidden");
  // Gated buttons stay in the row, hidden, so it does not reflow.
  assert.equal(toolbarPresence(p(CORE_PANEL_IDS.simulation), S(false, false), rest), "hidden");
  assert.equal(toolbarPresence(p(CORE_PANEL_IDS.options), S(true, true), rest), "shown");
  // The connection status is left out wherever it is not offered.
  assert.equal(toolbarPresence(p(CORE_PANEL_IDS.wsStatus), S(false, false), rest), "omit");
  assert.equal(toolbarPresence(p(CORE_PANEL_IDS.wsStatus), S(false, true), desktop), "shown");
});

test("mount rules restate Menu.tsx's render conditions", () => {
  const env = { restMode: true, websocketDeactivated: false };
  const ctx = (open: boolean, available = true, proceduralActive = false) => ({
    state: S(open, available),
    env,
    proceduralActive,
  });
  // Plain panels mount while open, offered or not (Menu.tsx does not check the button's gate).
  assert.equal(classicMounts(CORE_PANEL_IDS.options, ctx(true, false)), true);
  assert.equal(classicMounts(CORE_PANEL_IDS.scene, ctx(false)), false);
  // The tree is always mounted; it hides itself.
  assert.equal(classicMounts(CORE_PANEL_IDS.tree, ctx(false)), true);
  // One flag, two panels: storage when hosted, the server box otherwise.
  assert.equal(classicMounts(CORE_PANEL_IDS.storage, ctx(true, true)), true);
  assert.equal(classicMounts(CORE_PANEL_IDS.serverInfo, ctx(true, false)), false);
  // The cellbuilder panel is mounted for as long as a model is loaded, open or not.
  assert.equal(classicMounts(CORE_PANEL_IDS.cellbuilder, ctx(false, true, true)), true);
  assert.equal(classicMounts(CORE_PANEL_IDS.cellbuilder, ctx(true, true, false)), false);
  assert.equal(classicMounts(CORE_PANEL_IDS.equipmentAdmin, ctx(true, true, false)), false);
  assert.equal(classicMounts(CORE_PANEL_IDS.equipmentAdmin, ctx(true, true, true)), true);
  // Hosted windows only when hosted.
  const desktop = { state: S(true, true), env: { restMode: false }, proceduralActive: false };
  assert.equal(classicMounts(CORE_PANEL_IDS.admin, desktop), false);
  assert.equal(classicMounts(CORE_PANEL_IDS.attributeSearch, desktop), false);
  // The connection box never in a static embed.
  assert.equal(
    classicMounts(CORE_PANEL_IDS.wsStatus, { state: S(true, true), env: { websocketDeactivated: true }, proceduralActive: false }),
    false,
  );
});

test("every zone a core panel is placed in is a zone of the preset", () => {
  const zones = new Set(CLASSIC_ZONE_DEFS.map((z) => z.id));
  for (const p of corePanels()) {
    const pl = placementFor(p, CLASSIC_PRESET_ID);
    assert.ok(pl, `${p.id} has a classic placement`);
    assert.ok(zones.has(pl!.zone), `${p.id}: zone ${pl!.zone}`);
  }
  assert.ok(!zones.has(CLASSIC_ZONES.toolbar), "the top bar is chrome, not a zone");
});

test("the classic document opens nothing and knows every panel", () => {
  const doc = classicDefaults({ context: "default", panels: corePanels() });
  assert.equal(doc.preset, CLASSIC_PRESET_ID);
  for (const z of Object.values(doc.zones)) assert.deepEqual(z.panels, []);
  assert.deepEqual(doc.known, corePanels().map((p) => p.id));
});

test("every core panel's classic component is provided from the classic shell's graph", () => {
  const bodies = src("layout/classicBodies.ts");
  for (const [key, id] of Object.entries(CORE_PANEL_IDS)) {
    assert.match(bodies, new RegExp(`\\[CORE_PANEL_IDS\\.${key}\\]:`), `${id} is missing from classicBodies`);
  }
});

test("the catalogue reaches panel modules only through the classic shell module", () => {
  // A dynamic import from the catalogue (in the entry chunk) straight to a panel
  // module splits everything that module shares with the shell into new chunks.
  const catalogue = src("layout/corePanels.ts");
  const dynamic = [...catalogue.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(dynamic)], ["@/app"]);
  // ...and the shell module re-exports the loader they go through.
  assert.match(src("app.tsx"), /export \{[^}]*loadClassicPanel[^}]*\} from "@\/layout\/classicBodies"/);
});

test("split panels offer their frameless content", () => {
  for (const id of [
    CORE_PANEL_IDS.scene,
    CORE_PANEL_IDS.objectInfo,
    CORE_PANEL_IDS.storage,
    CORE_PANEL_IDS.options,
    CORE_PANEL_IDS.simulation,
    CORE_PANEL_IDS.cellbuilder,
    CORE_PANEL_IDS.componentControls,
    CORE_PANEL_IDS.nodeEditor,
    CORE_PANEL_IDS.tree,
  ]) {
    assert.equal(typeof getPanel(id)!.content, "function", `${id}.content`);
  }
});
