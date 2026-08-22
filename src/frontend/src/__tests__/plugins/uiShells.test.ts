import type React from "react";

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { registerPlugin, resetRegistry } from "@/plugins/registry";
import { register as registerUiAlt } from "@adapy-plugins/ui-alt";
import {
  CORE_UI_SHELL_ID,
  buildDefaultUiShellId,
  getUiShell,
  listUiShells,
  registerUiShell,
  resetUiShells,
  resolveUiShell,
  setBuildDefaultUiShellId,
  type UiShellSpec,
} from "@/plugins/uiShells";

function shell(id: string, extra: Partial<UiShellSpec> = {}): UiShellSpec {
  return {
    id,
    label: id.toUpperCase(),
    // The registry never invokes load() — resolution is pure bookkeeping.
    load: async () => ({ default: (() => null) as unknown as React.ComponentType }),
    ...extra,
  };
}

// Stand-in for the built-in registration (coreUiShell.ts imports @/app, which
// drags the whole viewer into a unit test).
function registerCore(): void {
  registerUiShell(shell(CORE_UI_SHELL_ID, { label: "Classic", order: 0 }), CORE_UI_SHELL_ID);
}

beforeEach(() => {
  resetRegistry();
  resetUiShells();
});

test("registers a shell and orders the list by (order, id)", () => {
  registerCore();
  registerUiShell(shell("zeta", { order: 5 }), "p1");
  registerUiShell(shell("alpha", { order: 5 }), "p2");
  assert.deepEqual(listUiShells().map((s) => s.id), [CORE_UI_SHELL_ID, "alpha", "zeta"]);
  assert.equal(getUiShell("alpha")?.pluginId, "p2");
  assert.equal(getUiShell(CORE_UI_SHELL_ID)?.builtin, true);
  assert.equal(getUiShell("alpha")?.builtin, false);
});

test("a plugin contributes shells through registerPlugin", () => {
  registerCore();
  registerPlugin({ id: "ui-alt", uiShells: [shell("alt")] });
  assert.deepEqual(listUiShells().map((s) => s.id), [CORE_UI_SHELL_ID, "alt"]);
  assert.equal(getUiShell("alt")?.pluginId, "ui-alt");
});

test("duplicate shell ids are ignored (first writer wins)", () => {
  registerUiShell(shell("alt", { label: "first" }), "p1");
  registerUiShell(shell("alt", { label: "second" }), "p2");
  assert.equal(listUiShells().length, 1);
  assert.equal(getUiShell("alt")?.label, "first");
});

test("a shell without an id or load() is rejected, not registered", () => {
  registerUiShell({ id: "", label: "x", load: async () => ({ default: null as never }) }, "p1");
  registerUiShell({ id: "broken", label: "x" } as unknown as UiShellSpec, "p1");
  assert.deepEqual(listUiShells(), []);
});

test("resolution precedence: url > storage > build default > core", () => {
  registerCore();
  registerUiShell(shell("alt"), "p1");
  registerUiShell(shell("other"), "p2");
  setBuildDefaultUiShellId("other");
  assert.equal(buildDefaultUiShellId(), "other");

  assert.deepEqual(resolveUiShell({ search: "?ui=alt", stored: "core" }), {
    id: "alt",
    source: "url",
    rejected: undefined,
  });
  assert.deepEqual(resolveUiShell({ search: "", stored: "alt" }), {
    id: "alt",
    source: "storage",
    rejected: undefined,
  });
  assert.deepEqual(resolveUiShell({ search: "", stored: null }), {
    id: "other",
    source: "build-default",
    rejected: undefined,
  });
  assert.deepEqual(resolveUiShell({ search: "", stored: null, buildDefault: null }), {
    id: CORE_UI_SHELL_ID,
    source: "builtin",
    rejected: undefined,
  });
});

test("?ui=core always reaches the built-in UI, whatever the build default is", () => {
  registerCore();
  registerUiShell(shell("alt"), "p1");
  setBuildDefaultUiShellId("alt");
  assert.equal(resolveUiShell({ search: "?ui=core", stored: "alt" }).id, CORE_UI_SHELL_ID);
});

test("an unregistered id falls through and is reported rather than blanking the UI", () => {
  registerCore();
  const res = resolveUiShell({ search: "?ui=nope", stored: null, buildDefault: null });
  assert.equal(res.id, CORE_UI_SHELL_ID);
  assert.equal(res.source, "builtin");
  assert.deepEqual(res.rejected, { id: "nope", source: "url" });
});

test("a build default naming a shell that was never registered still boots core", () => {
  registerCore();
  const res = resolveUiShell({ search: "", stored: null, buildDefault: "missing-ui" });
  assert.equal(res.id, CORE_UI_SHELL_ID);
  assert.deepEqual(res.rejected, { id: "missing-ui", source: "build-default" });
});

test("with core absent, resolution falls back to the first registered shell", () => {
  registerUiShell(shell("beta", { order: 20 }), "p1");
  registerUiShell(shell("alpha", { order: 10 }), "p2");
  assert.equal(resolveUiShell({ search: "", stored: null, buildDefault: null }).id, "alpha");
});

test("the ui-alt reference package registers a working shell descriptor", () => {
  registerCore();
  registerUiAlt();
  const alt = getUiShell("alt");
  assert.ok(alt, "ui-alt should contribute a shell with id 'alt'");
  assert.equal(alt.pluginId, "ui-alt");
  assert.equal(typeof alt.load, "function");
  // Both UIs offered => the switcher becomes visible in the menu bar.
  assert.equal(listUiShells().length, 2);
});
