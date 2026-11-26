// UiShellHost's crash fallback and its toasts had no tests at all. Covers:
//  - the banner names the shell that actually crashed, never a hard-coded literal name;
//  - a crash does not pin core past the mount it happened on: an ordinary reload (modelled here
//    as an unmount + a fresh mount, since that is what a reload actually does to this component's
//    state) gives the shell a genuine fresh try instead of re-forcing core from a stale
//    sessionStorage record, and never claims "Showing Classic" when it is not true;
//  - the switch-notice toast and its reason, and that it never fires away from core;
//  - the boot-rejection toast, when the resolved shell differs from the one asked for.
//
// Mounted for real with react-dom/client over a minimal DOM (fixtures/miniDom), same as
// shellSwitchInPlace.test.tsx. No DOM events are dispatchable here (miniDom's element-level
// addEventListener is a no-op), so every interaction goes through the exported functions
// (setActiveUiShell, …) directly, never a simulated click or change.

import assert from "node:assert/strict";
import { test } from "node:test";

const dom = await import("../fixtures/miniDom");
await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const U = await import("@/plugins/uiShells");
const { default: UiShellHost, UI_FALLBACK_KEY } = await import("@/plugins/UiShellHost");

const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  });

function freshRoot() {
  const el = dom.container();
  return { el, root: createRoot(el as unknown as Element) };
}

function cleanup() {
  localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
  sessionStorage.removeItem(UI_FALLBACK_KEY);
  dom.setLocation("http://localhost/");
  U.resetUiShells();
}

test("a shell crash falls back to core, with a banner named by the shell's own label, not a literal name", async () => {
  U.resetUiShells();
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic" }, "core");
  U.registerUiShell({ id: "docked", label: "Fancy Shell" }, "p");
  localStorage.setItem(U.UI_SHELL_STORAGE_KEY, "docked");

  const Root: React.FC<{ shellId: string }> = ({ shellId }) => {
    if (shellId === "docked") throw new Error("boom");
    return React.createElement("div", { "data-probe": shellId });
  };
  const { el, root } = freshRoot();
  try {
    await act(async () => root.render(React.createElement(UiShellHost, { coreRoot: () => Promise.resolve({ default: Root }) })));
    await flush();

    assert.equal(el.querySelector("[data-probe]")?.getAttribute("data-probe"), U.CORE_UI_SHELL_ID, "fell back to core");
    const banner = el.querySelector('[role="alert"]');
    assert.ok(banner, "the banner is shown");
    const text = banner!.textContent;
    assert.match(text, /Fancy Shell crashed: boom\. Showing Classic\./);
    assert.doesNotMatch(text, /Docked UI/i, "never the literal \"Docked UI\" name");
    assert.equal(sessionStorage.getItem(UI_FALLBACK_KEY) !== null, true, "the crash is recorded for a reload to read back");
  } finally {
    await act(async () => root.unmount());
    cleanup();
  }
});

test("an ordinary reload after a crash gives the shell a fresh try: it is not re-forced into core, and the one-time notice never claims Showing Classic", async () => {
  U.resetUiShells();
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic" }, "core");
  U.registerUiShell({ id: "docked", label: "Fancy Shell" }, "p");
  localStorage.setItem(U.UI_SHELL_STORAGE_KEY, "docked");

  let shouldThrow = true;
  const Root: React.FC<{ shellId: string }> = ({ shellId }) => {
    if (shellId === "docked" && shouldThrow) throw new Error("stale chunk");
    return React.createElement("div", { "data-probe": shellId });
  };
  const coreRoot = () => Promise.resolve({ default: Root });

  // First mount: it crashes, as above.
  let { el, root } = freshRoot();
  await act(async () => root.render(React.createElement(UiShellHost, { coreRoot })));
  await flush();
  assert.equal(el.querySelector("[data-probe]")?.getAttribute("data-probe"), U.CORE_UI_SHELL_ID);
  assert.ok(sessionStorage.getItem(UI_FALLBACK_KEY));
  await act(async () => root.unmount());

  // The underlying cause (a stale chunk) is gone by the time the page reloads -- exactly the
  // ordinary case a plain F5 is meant to recover from. A fresh mount is what a reload actually
  // gives this component: a brand-new instance, so `crashed` starts false regardless of what is
  // still sitting in sessionStorage (the bug this fixes: a React-state-only `crashed`, never
  // seeded from the stored record, is what makes that possible).
  shouldThrow = false;
  ({ el, root } = freshRoot());
  try {
    await act(async () => root.render(React.createElement(UiShellHost, { coreRoot })));
    await flush();

    assert.equal(el.querySelector("[data-probe]")?.getAttribute("data-probe"), "docked", "the real shell shows this time, not core");
    assert.equal(el.querySelector('[role="alert"]'), null, "no banner: nothing is forcing core now");
    const toast = el.querySelector('[role="status"]');
    assert.ok(toast, "a one-time recovered-from notice still appears");
    const toastText = toast!.textContent;
    assert.match(toastText, /Fancy Shell crashed before this reload/);
    assert.doesNotMatch(toastText, /Showing Classic/, "Classic is not what is on screen now, so the notice must not say so");
    assert.equal(sessionStorage.getItem(UI_FALLBACK_KEY), null, "the one-time record is consumed, not left to describe a later crash too");
  } finally {
    await act(async () => root.unmount());
    cleanup();
  }
});

test("switching shells carries a reason and shows a toast; switching FROM core shows none (Classic's own switch path is unchanged)", async () => {
  U.resetUiShells();
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic" }, "core");
  U.registerUiShell({ id: "desk", label: "Desk" }, "core");
  const Root: React.FC<{ shellId: string }> = ({ shellId }) => React.createElement("div", { "data-probe": shellId });
  const { el, root } = freshRoot();
  try {
    await act(async () => root.render(React.createElement(UiShellHost, { coreRoot: () => Promise.resolve({ default: Root }) })));
    await flush();
    assert.equal(el.querySelector('[role="status"]'), null, "nothing yet");

    // core -> desk: an in-place switch away from core never shows a toast (Classic unchanged).
    await act(async () => U.setActiveUiShell("desk", { reason: "palette" }));
    await flush();
    assert.equal(el.querySelector('[role="status"]'), null, "no toast leaving core");

    // desk -> core: now there is something to announce.
    await act(async () => U.setActiveUiShell(U.CORE_UI_SHELL_ID, { reason: "switcher" }));
    await flush();
    const toast = el.querySelector('[role="status"]');
    assert.ok(toast, "a toast names the switch");
    assert.match(toast!.textContent, /Switched to Classic \(interface switcher\)/);
  } finally {
    await act(async () => root.unmount());
    cleanup();
  }
});

test("a boot rejection (the stored/url shell was never registered) shows a toast naming what was asked for and what is shown instead", async () => {
  U.resetUiShells();
  dom.setLocation("http://localhost/?ui=nope");
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic" }, "core");
  const Root: React.FC<{ shellId: string }> = ({ shellId }) => React.createElement("div", { "data-probe": shellId });
  const { el, root } = freshRoot();
  try {
    await act(async () => root.render(React.createElement(UiShellHost, { coreRoot: () => Promise.resolve({ default: Root }) })));
    await flush();
    const toast = el.querySelector('[role="status"]');
    assert.ok(toast, "the boot rejection is reported, not just a console warning");
    assert.match(toast!.textContent, /UI "nope" is not available.*showing Classic/);
  } finally {
    await act(async () => root.unmount());
    cleanup();
  }
});
