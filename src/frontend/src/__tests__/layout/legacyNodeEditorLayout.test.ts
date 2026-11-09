// A layout saved before the node editor was removed still names its panel: loading it must not
// fail, and the retired id must not resurface as a registered panel.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { migrateLayoutPrefs, LAYOUT_PREFS_VERSION } = await import("@/layout/layoutStore");
const { migrateLayoutDoc } = await import("@/layout/reducers");
const { registerCorePanels } = await import("@/layout/corePanels");
const { getPanel } = await import("@/layout/registry");
const { CORE_PANEL_IDS } = await import("@/layout/corePanelIds");

const RETIRED = "core:node-editor";

test("saved prefs that mention the retired panel load cleanly", () => {
  const doc = {
    v: 1, preset: "classic", zones: { z: { panels: [RETIRED, "core:tree"], active: RETIRED } },
    floats: { [RETIRED]: { x: 1, y: 1, w: 300, h: 200, z: 1 } }, minimised: [RETIRED], known: [RETIRED],
  };
  const prefs = migrateLayoutPrefs({ v: LAYOUT_PREFS_VERSION, docs: { classic: doc }, pinned: { desktop: [RETIRED] } });
  assert.ok(prefs.docs.classic);
  assert.doesNotThrow(() => migrateLayoutDoc(JSON.parse(JSON.stringify(doc)), "classic", [{ id: "z" } as never]));
});

test("the retired panel is not registered", () => {
  registerCorePanels();
  assert.equal(getPanel(RETIRED), undefined);
  assert.ok(!Object.values(CORE_PANEL_IDS).includes(RETIRED as never));
});