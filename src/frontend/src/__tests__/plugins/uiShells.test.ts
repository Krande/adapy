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
  runtimeDefaultUiShellId,
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

test("resolution precedence: url > storage > build default > core (no runtime value)", () => {
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

// ---------------------------------------------------------------------------
// Runtime default (window.ADA_UI_DEFAULT, served by /config.js). It sits below
// the two user-driven sources and above the build-time constant, so one image
// can serve several deployments and a UI rollback is a config edit rather than
// an image rebuild.
// ---------------------------------------------------------------------------

/** Run `fn` with a stubbed global `window`, restoring whatever was there. */
function withWindow<T>(win: unknown, fn: () => T): T {
  const g = globalThis as { window?: unknown };
  const had = "window" in g;
  const prev = g.window;
  g.window = win;
  try {
    return fn();
  } finally {
    if (had) g.window = prev;
    else delete g.window;
  }
}

test("the runtime default outranks the build default but loses to storage and ?ui=", () => {
  registerCore();
  registerUiShell(shell("alt"), "p1");
  registerUiShell(shell("other"), "p2");
  setBuildDefaultUiShellId("other");

  assert.deepEqual(resolveUiShell({ search: "", stored: null, runtimeDefault: "alt" }), {
    id: "alt",
    source: "runtime-default",
    rejected: undefined,
  });
  // A user's sticky choice is still the user's choice.
  assert.equal(resolveUiShell({ search: "", stored: "other", runtimeDefault: "alt" }).source, "storage");
  // ...and the per-tab escape hatch still reaches the built-in UI.
  assert.equal(resolveUiShell({ search: "?ui=core", stored: null, runtimeDefault: "alt" }).id, CORE_UI_SHELL_ID);
});

test("no runtime value leaves the build-time default in charge (existing images unchanged)", () => {
  registerCore();
  registerUiShell(shell("alt"), "p1");
  setBuildDefaultUiShellId("alt");

  for (const runtimeDefault of [null, "", "   "]) {
    const res = resolveUiShell({ search: "", stored: null, runtimeDefault });
    assert.equal(res.id, "alt", `runtimeDefault=${JSON.stringify(runtimeDefault)}`);
    assert.equal(res.source, "build-default");
  }
});

test("a runtime default naming an unregistered shell is reported and falls through", () => {
  registerCore();
  registerUiShell(shell("alt"), "p1");
  setBuildDefaultUiShellId("alt");

  // The whole risk this level introduces: a typo now reaches a live deployment
  // instead of failing the build. Worst case is the next source down.
  const res = resolveUiShell({ search: "", stored: null, runtimeDefault: "typo-ui" });
  assert.equal(res.id, "alt");
  assert.equal(res.source, "build-default");
  assert.deepEqual(res.rejected, { id: "typo-ui", source: "runtime-default" });

  // ...and with nothing below it either, the built-in UI. Never a blank page.
  const bare = resolveUiShell({ search: "", stored: null, runtimeDefault: "typo-ui", buildDefault: null });
  assert.equal(bare.id, CORE_UI_SHELL_ID);
  assert.equal(bare.source, "builtin");
  assert.deepEqual(bare.rejected, { id: "typo-ui", source: "runtime-default" });
});

test("the runtime default is read from window.ADA_UI_DEFAULT when not injected", () => {
  registerCore();
  registerUiShell(shell("alt"), "p1");

  withWindow({ ADA_UI_DEFAULT: " alt " }, () => {
    assert.equal(runtimeDefaultUiShellId(), "alt");
    assert.equal(resolveUiShell({ search: "", stored: null, buildDefault: null }).id, "alt");
  });
});

test("an absent, blank or non-string window.ADA_UI_DEFAULT is not a value", () => {
  registerCore();
  registerUiShell(shell("alt"), "p1");
  setBuildDefaultUiShellId("alt");

  const notValues = [
    {},
    { ADA_UI_DEFAULT: "" },
    { ADA_UI_DEFAULT: "  " },
    { ADA_UI_DEFAULT: 42 },
    { ADA_UI_DEFAULT: null },
  ];
  for (const win of notValues) {
    withWindow(win, () => {
      assert.equal(runtimeDefaultUiShellId(), null, JSON.stringify(win));
      assert.equal(resolveUiShell({ search: "", stored: null }).source, "build-default");
    });
  }

  // No `window` at all (node, embed worker) must not throw either.
  assert.equal(runtimeDefaultUiShellId(), null);
});
