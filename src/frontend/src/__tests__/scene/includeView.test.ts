// The builder's include view: what the build leaves out (INCLUDE false, or its structure's) is drawn
// faint with dashed edges (the default), hidden ("Included only"), or like the rest ("All"). Three
// radio commands set it, remembered in the browser. (The scene's drawing of it, meshRebuild and
// overlays.refreshFaceStyles, is checked in the browser: building the builder scene needs a canvas.)

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { includeLook } = await import("@/utils/cellbuilder/include");
const { useIncludeViewStore } = await import("@/state/includeViewStore");
const C = await import("@/layout/commands");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { registerCoreCommands } = await import("@/layout/coreCommands");

test("the look: drawn, dimmed or hidden, by the include state and the view", () => {
  assert.deepEqual(includeLook("included", "included"), { visible: true, dimmed: false });
  assert.deepEqual(includeLook("excluded", "dimmed"), { visible: true, dimmed: true });
  assert.deepEqual(includeLook("excluded-by-structure", "dimmed"), { visible: true, dimmed: true });
  assert.deepEqual(includeLook("excluded", "included"), { visible: false, dimmed: false });
  assert.deepEqual(includeLook("excluded-by-structure", "all"), { visible: true, dimmed: false });
});

test("three radio commands set the view, remembered in the browser; dimmed is the default", () => {
  C.resetCommands();
  registerCoreCommands();
  useIncludeViewStore.getState().setView("dimmed");
  const ids = [ID.builderIncludeDimmed, ID.builderIncludeOnly, ID.builderIncludeAll];
  assert.deepEqual(ids.map((id) => C.getCommand(id)?.checked?.()), [true, false, false]);
  assert.equal(window.localStorage.getItem("ada:builder-include-view"), null, "the default writes nothing");
  assert.ok(C.getCommand(ID.builderIncludeOnly)!.disabledReason?.(), "greyed with no procedural model");
  C.getCommand(ID.builderIncludeOnly)!.run();
  assert.equal(useIncludeViewStore.getState().view, "included");
  assert.equal(window.localStorage.getItem("ada:builder-include-view"), "included");
  assert.deepEqual(ids.map((id) => C.getCommand(id)?.checked?.()), [false, true, false]);
  let told = 0;
  const off = C.getCommand(ID.builderIncludeAll)!.watch!(() => told++);
  useIncludeViewStore.getState().setView("all");
  off();
  assert.ok(told > 0, "its toolbar button redraws");
  useIncludeViewStore.getState().setView("dimmed");
  assert.equal(window.localStorage.getItem("ada:builder-include-view"), null);
  C.resetCommands();
});
