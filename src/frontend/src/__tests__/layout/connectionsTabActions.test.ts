// Opening the Connections panel on one of its tabs, and the Scene panel's old modes of those tabs
// sent there: Clashes, the joints review, the joint overlays and the builder were the Scene panel's
// Joints group, and an opener that still sets one of their modes lands on the Connections panel.

import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, test } from "node:test";

await import("../fixtures/panelRealm");

const { CORE_PANEL_IDS, isPanelOpen } = await import("@/layout/panelActions");
const { listPanels, resetPanelRegistry } = await import("@/layout/registry");
const { registerCorePanels } = await import("@/layout/corePanels");
const { openConnectionsTab, redirectMovedSceneModes, SCENE_MODE_TO_CONNECTIONS_TAB } = await import(
  "@/layout/connectionsTabActions"
);
const { useComponentControlsStore } = await import("@/state/componentControlsStore");
const { BUILDER_TAB, CLASHES_TAB, JOINT_OVERLAYS_TAB, JOINTS_REVIEW_TAB, useConnectionBuilderTabStore } = await import(
  "@/state/connectionBuilderTabStore"
);
const { useConnectionsOfferStore } = await import("@/state/connectionsOfferStore");
const { useSceneInfoStore } = await import("@/state/sceneInfoStore");

const tab = () => useConnectionBuilderTabStore.getState().tab;
const open = () => useComponentControlsStore.getState().isVisible;

afterEach(() => {
  useComponentControlsStore.getState().setIsVisible(false);
  useConnectionBuilderTabStore.getState().select(BUILDER_TAB);
  useSceneInfoStore.getState().setMode("info");
});

test("openConnectionsTab picks the tab and reveals the Connections panel", () => {
  assert.equal(open(), false);
  assert.equal(openConnectionsTab(CLASHES_TAB), true);
  assert.equal(tab(), CLASHES_TAB);
  assert.equal(open(), true);
  assert.equal(isPanelOpen(CORE_PANEL_IDS.componentControls), true);
});

test("an opener of the old Clashes mode lands on the Connections panel's Clashes tab", () => {
  const off = redirectMovedSceneModes();
  try {
    useSceneInfoStore.getState().setMode("section");
    // What an opener of the Scene panel's Clashes tab did.
    useSceneInfoStore.getState().setMode("clashes");
    assert.equal(tab(), CLASHES_TAB);
    assert.equal(open(), true, "the Connections panel is revealed");
    assert.equal(useSceneInfoStore.getState().mode, "section", "the Scene panel keeps the tab it had");
  } finally {
    off();
  }
});

test("every old mode of a moved tab is sent to its tab; other modes are left alone", () => {
  assert.deepEqual({ ...SCENE_MODE_TO_CONNECTIONS_TAB }, {
    connection: BUILDER_TAB,
    clashes: CLASHES_TAB,
    joints: JOINTS_REVIEW_TAB,
    jointOverlays: JOINT_OVERLAYS_TAB,
  });
  const off = redirectMovedSceneModes();
  try {
    for (const [mode, want] of Object.entries(SCENE_MODE_TO_CONNECTIONS_TAB)) {
      useComponentControlsStore.getState().setIsVisible(false);
      useSceneInfoStore.getState().setMode(mode as never);
      assert.equal(tab(), want, mode);
      assert.equal(open(), true, mode);
      assert.equal(useSceneInfoStore.getState().mode, "info", `${mode}: the Scene panel is back on Model`);
    }
    useComponentControlsStore.getState().setIsVisible(false);
    useSceneInfoStore.getState().setMode("mesh");
    assert.equal(open(), false, "a Scene panel mode of its own opens nothing else");
    assert.equal(useSceneInfoStore.getState().mode, "mesh");
  } finally {
    off();
  }
  // Unsubscribed: the mode is the store's again.
  useSceneInfoStore.getState().setMode("clashes");
  assert.equal(open(), false);
});

test("a moved mode set before the redirect was installed is sent too", () => {
  useSceneInfoStore.getState().setMode("joints");
  const off = redirectMovedSceneModes();
  try {
    assert.equal(tab(), JOINTS_REVIEW_TAB);
    assert.equal(open(), true);
    assert.equal(useSceneInfoStore.getState().mode, "info");
  } finally {
    off();
  }
});

test("the Connections panel is offered with nothing but its Clashes and Overlays tabs", () => {
  resetPanelRegistry();
  registerCorePanels();
  useConnectionsOfferStore.getState().setOffer(0);
  // No builder, no plugin tab, no joints, no model: still offered, in every kind of viewer.
  for (const env of [{ restMode: true }, { restMode: false, websocketDeactivated: false }, { restMode: false, websocketDeactivated: true }]) {
    assert.ok(listPanels(env).some((p) => p.id === CORE_PANEL_IDS.componentControls), JSON.stringify(env));
  }
});

test("core opens the moved tabs through the Connections panel, never through the Scene panel's mode", () => {
  const root = join(process.cwd(), "src");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) {
        if (name !== "__tests__") walk(p);
      } else if (/\.(ts|tsx)$/.test(name)) files.push(p);
    }
  };
  walk(root);
  const setsMovedMode = /setMode\(\s*["'](clashes|joints|jointOverlays|connection)["']\s*\)/;
  const offenders = files.filter((f) => setsMovedMode.test(readFileSync(f, "utf8"))).map((f) => f.slice(root.length + 1));
  assert.deepEqual(offenders, []);
});
