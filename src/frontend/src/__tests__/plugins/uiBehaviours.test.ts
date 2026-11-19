// UI behaviours (plugins/uiBehaviours): what core does that one UI wants and another might not.
// Each has a default for core's own UI and one for every other shell; a shell chooses its own with
// `UiShellSpec.behaviours`, and core reads the UI on screen's.
//
// THE DEFAULTS ARE PINNED HERE. Changing one changes every UI that has not chosen -- the Docked UI
// and other plugin shells included -- so it must show up as a change to this test in a review.

import assert from "node:assert/strict";
import { test } from "node:test";

const U = await import("@/plugins/uiShells");
const B = await import("@/plugins/uiBehaviours");

const PIN = "builder.pickHiddenWhileCellsHidden" as const;

/** Core's own UI, the Desktop-like shell that says nothing, and two shells that choose. */
function registerShells(): void {
  U.resetUiShells();
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", load: () => Promise.resolve({ default: () => null }) }, "core");
  U.registerUiShell({ id: "quiet", label: "Quiet" }, "core");
  U.registerUiShell({ id: "off", label: "Off", behaviours: { [PIN]: false } }, "plugin-a");
  U.registerUiShell({ id: "own-root", label: "Own root", load: () => Promise.resolve({ default: () => null }), behaviours: { [PIN]: false } }, "plugin-b");
}

test("the catalogue's defaults, per kind of UI (a change here changes every UI that has not chosen)", () => {
  const defaults = Object.fromEntries(Object.entries(B.UI_BEHAVIOURS).map(([id, d]) => [id, { core: d.core, shells: d.shells }]));
  assert.deepEqual(defaults, {
    "builder.pickHiddenWhileCellsHidden": { core: true, shells: true },
  });
  for (const d of Object.values(B.UI_BEHAVIOURS)) assert.ok(d.description.length > 20, "each says what it does");
});

test("a shell that says nothing gets the default for its kind of UI", () => {
  registerShells();
  assert.equal(B.uiBehaviourFor(U.CORE_UI_SHELL_ID, PIN), B.UI_BEHAVIOURS[PIN].core);
  assert.equal(B.uiBehaviourFor("quiet", PIN), B.UI_BEHAVIOURS[PIN].shells);
  // A shell not registered (yet) is a shell like any other.
  assert.equal(B.uiBehaviourFor("missing", PIN), B.UI_BEHAVIOURS[PIN].shells);
  U.resetUiShells();
});

test("a shell's own choice wins, composed or with a root of its own, and touches no other UI", () => {
  registerShells();
  assert.equal(B.uiBehaviourFor("off", PIN), false);
  assert.equal(B.uiBehaviourFor("own-root", PIN), false);
  assert.equal(B.uiBehaviourFor(U.CORE_UI_SHELL_ID, PIN), true, "core's own UI keeps its default");
  assert.equal(B.uiBehaviourFor("quiet", PIN), true, "another shell keeps its default");
  U.resetUiShells();
});

test("uiBehaviour reads the UI on screen", () => {
  registerShells();
  U.setBuildDefaultUiShellId("off");
  assert.equal(U.activeUiShellId(), "off");
  assert.equal(B.uiBehaviour(PIN), false);
  U.resetUiShells();
  registerShells();
  U.setBuildDefaultUiShellId(U.CORE_UI_SHELL_ID);
  assert.equal(B.uiBehaviour(PIN), true);
  U.resetUiShells();
});

test("names this core does not have are listed, and ignored", () => {
  assert.deepEqual(B.unknownUiBehaviours({ [PIN]: true, "from.a.newer.core": true }), ["from.a.newer.core"]);
  assert.deepEqual(B.unknownUiBehaviours(undefined), []);
  assert.equal(B.isUiBehaviour(PIN), true);
  assert.equal(B.isUiBehaviour("from.a.newer.core"), false);
  assert.equal(B.isUiBehaviour("toString"), false, "not fooled by the object's own members");
});
