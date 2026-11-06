// A UI-shell switch that cannot happen in place reloads the page. After a
// deep-linked load the address bar is bare (useUrlParamLoad strips
// ?scope&file), so the reload must put the stripped params back or the other
// shell boots with an empty scene. (Ported from #484, which predates the
// in-place switch.)

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { carryDeepLinkOnShellSwitch } from "@/hooks/deepLinkCarry";
import {
  carryOnShellSwitch,
  resetUiShells,
  setActiveUiShell,
  shellSwitchUrl,
} from "@/plugins/uiShells";

beforeEach(() => {
  resetUiShells();
});

function query(href: string): Record<string, string> {
  return Object.fromEntries(new URL(href).searchParams);
}

test("with nothing carried, a switch reloads the same URL without ?ui=", () => {
  assert.equal(
    shellSwitchUrl("https://viewer.example/?ui=core&tab=2"),
    "https://viewer.example/?tab=2",
  );
  assert.equal(shellSwitchUrl("https://viewer.example/"), "https://viewer.example/");
});

test("carried params are added to the reload URL", () => {
  carryOnShellSwitch(() => ({ scope: "user:me", file: "deck.sin" }));
  assert.deepEqual(query(shellSwitchUrl("https://viewer.example/")), {
    scope: "user:me",
    file: "deck.sin",
  });
});

test("a param already in the URL wins, and ui is never carried", () => {
  carryOnShellSwitch(() => ({ file: "old.step", ui: "core", mode: "a" }));
  assert.deepEqual(query(shellSwitchUrl("https://viewer.example/?file=new.step&ui=alt")), {
    file: "new.step",
    mode: "a",
  });
});

test("a provider answering null carries nothing, and a disposed one is not asked", () => {
  carryOnShellSwitch(() => null);
  let asked = 0;
  const dispose = carryOnShellSwitch(() => {
    asked += 1;
    return { file: "deck.sin" };
  });
  dispose();
  assert.equal(shellSwitchUrl("https://viewer.example/"), "https://viewer.example/");
  assert.equal(asked, 0);
});

test("a throwing provider is skipped, not the switch", () => {
  const warn = console.warn;
  console.warn = () => {};
  try {
    carryOnShellSwitch(() => {
      throw new Error("boom");
    });
    carryOnShellSwitch(() => ({ file: "deck.sin" }));
    assert.deepEqual(query(shellSwitchUrl("https://viewer.example/")), { file: "deck.sin" });
  } finally {
    console.warn = warn;
  }
});

test("resetUiShells forgets the carries", () => {
  carryOnShellSwitch(() => ({ file: "deck.sin" }));
  resetUiShells();
  assert.equal(shellSwitchUrl("https://viewer.example/"), "https://viewer.example/");
});

test("a reloading setActiveUiShell goes to the URL with the carried params", () => {
  const g = globalThis as { window?: unknown };
  const had = "window" in g;
  const prev = g.window;
  const replaced: string[] = [];
  g.window = {
    location: {
      href: "https://viewer.example/?ui=core",
      replace: (url: string) => replaced.push(url),
      reload: () => assert.fail("reload() instead of replace()"),
    },
  };
  try {
    carryOnShellSwitch(() => ({ scope: "user:me", file: "deck.sin" }));
    // No in-place host is mounted here: the switch reloads.
    setActiveUiShell("alt");
    assert.equal(replaced.length, 1);
    assert.deepEqual(query(replaced[0]), { scope: "user:me", file: "deck.sin" });
  } finally {
    if (had) g.window = prev;
    else delete g.window;
    try {
      localStorage?.removeItem("ada:ui");
    } catch {
      // No storage in this runtime; nothing was written.
    }
  }
});

test("a deep link is carried whole while its load runs, minus ui", () => {
  carryDeepLinkOnShellSwitch(
    "?scope=user:me&file=deck.sin&derived=_derived/x.glb&mode=a&ui=core",
    () => false,
  );
  assert.deepEqual(query(shellSwitchUrl("https://viewer.example/")), {
    scope: "user:me",
    file: "deck.sin",
    derived: "_derived/x.glb",
    mode: "a",
  });
});

test("once settled, a deep link is carried only while its file is loaded", () => {
  let loaded = true;
  const carry = carryDeepLinkOnShellSwitch("?file=deck.sin", () => loaded);
  carry.settle();
  assert.deepEqual(query(shellSwitchUrl("https://viewer.example/")), { file: "deck.sin" });
  loaded = false; // the user closed the model
  assert.equal(shellSwitchUrl("https://viewer.example/"), "https://viewer.example/");
});

test("a disposed deep-link carry is not carried", () => {
  const carry = carryDeepLinkOnShellSwitch("?file=deck.sin", () => true);
  carry.dispose();
  assert.equal(shellSwitchUrl("https://viewer.example/"), "https://viewer.example/");
});
