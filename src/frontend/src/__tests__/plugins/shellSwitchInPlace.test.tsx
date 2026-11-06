// A UI-shell switch between core's own UI and composed shells happens in place:
// core's root (and with it the viewer provider and the 3D canvas) stays mounted,
// and only the shell's layout, chrome and registrations change. A switch to a
// shell with a root of its own still reloads. A mode switch inside a shell keeps
// the scene too.
//
// Mounted for real with react-dom/client over a minimal DOM (fixtures/miniDom).

import assert from "node:assert/strict";
import { test } from "node:test";

const dom = await import("../fixtures/miniDom");
await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const U = await import("@/plugins/uiShells");
const { default: UiShellHost, CORE_ROOT_KEY, shellRootKey } = await import("@/plugins/UiShellHost");
const { default: ComposedLayout } = await import("@/layout/ComposedLayout");
const C = await import("@/layout/commands");
const S = await import("@/layout/shortcuts");
const O = await import("@/layout/viewportOverlays");
const M = await import("@/layout/shellModes");
const P = await import("@/layout/presets/classicPlace");
const { listPanels, registerPanel } = await import("@/layout/registry");
const { useModelState } = await import("@/state/modelState");
const { useSelectedObjectStore } = await import("@/state/useSelectedObjectStore");

/** Counts its mounts, and stands where the layout (and so the canvas) would. */
function counter() {
  const seen = { mounts: 0, unmounts: 0 };
  const Probe: React.FC<{ label?: string }> = ({ label }) => {
    React.useEffect(() => {
      seen.mounts++;
      return () => {
        seen.unmounts++;
      };
    }, []);
    return React.createElement("div", { "data-probe": label ?? "" });
  };
  return { seen, Probe };
}

const flush = () => act(async () => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
});

function header(attr: string) {
  return () => Promise.resolve({ default: () => React.createElement("div", { [attr]: "" }) });
}

test("the host keys every shell under core's root alike, and a shell with its own root apart", () => {
  U.resetUiShells();
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", load: () => Promise.resolve({ default: () => null }) }, "core");
  U.registerUiShell({ id: "desk", label: "Desk" }, "core");
  U.registerUiShell({ id: "own", label: "Own", load: () => Promise.resolve({ default: () => null }) }, "p");
  U.registerUiShell({ id: "fussy", label: "Fussy", reloadOnSwitch: true }, "p");
  assert.equal(shellRootKey(U.CORE_UI_SHELL_ID), CORE_ROOT_KEY);
  assert.equal(shellRootKey("desk"), CORE_ROOT_KEY);
  assert.notEqual(shellRootKey("own"), CORE_ROOT_KEY);
  assert.notEqual(shellRootKey("fussy"), CORE_ROOT_KEY);
  assert.equal(U.canSwitchInPlace("core", "desk"), true);
  assert.equal(U.canSwitchInPlace("desk", "core"), true);
  assert.equal(U.canSwitchInPlace("desk", "own"), false);
  assert.equal(U.canSwitchInPlace("desk", "fussy"), false);
  assert.equal(U.canSwitchInPlace("desk", "missing"), false);
  U.resetUiShells();
});

test("Classic -> Desktop -> Classic happens in place: core's root is mounted once; a shell with its own root reloads", async () => {
  U.resetUiShells();
  dom.navigations.length = 0;
  dom.setLocation("http://localhost/?ui=core&tab=2");
  localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", load: () => Promise.resolve({ default: () => null }) }, "core");
  U.registerUiShell({ id: "desk", label: "Desk" }, "core");
  U.registerUiShell({ id: "own", label: "Own", load: () => Promise.resolve({ default: () => React.createElement("div", { "data-own": "" }) }) }, "p");

  const { seen, Probe } = counter();
  const shown: string[] = [];
  const Root: React.FC<{ shellId: string }> = ({ shellId }) => {
    shown.push(shellId);
    return React.createElement(Probe, { label: shellId });
  };
  const el = dom.container();
  const root = createRoot(el as unknown as Element);
  await act(async () => root.render(React.createElement(UiShellHost, { coreRoot: () => Promise.resolve({ default: Root }) })));
  await flush();
  const node = el.querySelector("[data-probe]");
  assert.equal(node?.getAttribute("data-probe"), "core");
  assert.equal(seen.mounts, 1);

  await act(async () => U.setActiveUiShell("desk"));
  await flush();
  assert.deepEqual(dom.navigations, [], "no navigation");
  assert.equal(U.activeUiShellId(), "desk");
  assert.equal(U.bootUiShellId(), "core", "the page still booted in core");
  assert.equal(localStorage.getItem(U.UI_SHELL_STORAGE_KEY), "desk", "persisted");
  assert.equal(window.location.search, "?tab=2", "?ui= is dropped from the address bar, the rest kept");
  assert.equal(el.querySelector("[data-probe]"), node, "the same element: nothing below core's root was remounted");
  assert.equal(node?.getAttribute("data-probe"), "desk");
  assert.deepEqual(seen, { mounts: 1, unmounts: 0 });

  await act(async () => U.setActiveUiShell(null));
  await flush();
  assert.equal(U.activeUiShellId(), "core", "null: back to the default");
  assert.equal(el.querySelector("[data-probe]"), node);
  assert.deepEqual(seen, { mounts: 1, unmounts: 0 });
  assert.deepEqual(dom.navigations, []);

  await act(async () => U.setActiveUiShell("own"));
  await flush();
  assert.equal(dom.navigations.length, 1, "a shell with its own root: a reload");
  assert.equal(sessionStorage.getItem(U.UI_SHELL_SWITCH_RELOAD_KEY), "1", "marked as a switch's reload");
  assert.equal(el.querySelector("[data-probe]")?.getAttribute("data-probe"), "core", "the page shows core until the reload lands");

  await act(async () => root.unmount());
  sessionStorage.removeItem(U.UI_SHELL_SWITCH_RELOAD_KEY);
  localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
  dom.setLocation("http://localhost/");
  U.resetUiShells();
});

test("without a mounted host, or when the host throws, a switch reloads", () => {
  U.resetUiShells();
  dom.navigations.length = 0;
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", load: () => Promise.resolve({ default: () => null }) }, "core");
  U.registerUiShell({ id: "desk", label: "Desk" }, "core");
  const warn = console.warn;
  console.warn = () => {};
  try {
    U.setActiveUiShell("desk");
    assert.equal(dom.navigations.length, 1);
    const undo = U.setUiShellSwitchHost(() => {
      throw new Error("boom");
    });
    U.setActiveUiShell("core");
    assert.equal(dom.navigations.length, 2);
    undo();
    let asked = 0;
    U.setUiShellSwitchHost(() => (asked++, true));
    U.setActiveUiShell("desk", { reload: false });
    assert.equal(asked, 0, "reload: false only persists");
    assert.equal(dom.navigations.length, 2);
  } finally {
    console.warn = warn;
    localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
    sessionStorage.removeItem(U.UI_SHELL_SWITCH_RELOAD_KEY);
    U.resetUiShells();
  }
});

test("a switch's reload is told once to the page it loads, never to a later reload", () => {
  U.resetUiShells();
  sessionStorage.setItem(U.UI_SHELL_SWITCH_RELOAD_KEY, "1");
  assert.equal(U.loadedByShellSwitch(), true);
  assert.equal(sessionStorage.getItem(U.UI_SHELL_SWITCH_RELOAD_KEY), null, "the marker is gone");
  assert.equal(U.loadedByShellSwitch(), true, "the same answer for the rest of the page");
  U.resetUiShells();
  assert.equal(U.loadedByShellSwitch(), false, "the next page: a plain reload");
});

test("an in-place switch swaps the layout's shell without remounting the layout, and cleans up after the one it leaves", async () => {
  U.resetUiShells();
  const body = () => Promise.resolve({ default: () => null });
  registerPanel({ id: "t:hidden-by-desk", title: "Hidden", body }, "test");
  const desk: import("@/plugins/uiShells").UiShellSpec = {
    id: "t-desk",
    label: "Desk",
    layout: { modes: [{ id: "model", label: "Model" }, { id: "results", label: "Results" }] },
    chrome: { header: header("data-desk-header"), toolbar: false },
    panels: { "t:hidden-by-desk": { available: false } },
  };
  // A plugin's shell whose chrome registers a HUD when its module loads (once a page) and a command while mounted.
  let headerModuleLoaded = false;
  const plugHeader = () => {
    if (!headerModuleLoaded) {
      headerModuleLoaded = true;
      O.registerViewportOverlay({ id: "plug:hud", anchor: "top-left", render: () => null }, "plug");
    }
    const H = () => {
      React.useEffect(() => {
        C.registerCommand({ id: "plug:do", label: "Do", keys: "Shift+Y", run() {} }, "plug");
      }, []);
      return React.createElement("div", { "data-plug-header": "" });
    };
    return Promise.resolve({ default: H });
  };
  // What the plugin had before its shell came up stays.
  C.registerCommand({ id: "plug:feature", label: "Feature", run() {} }, "plug");
  U.registerUiShell({ id: "t-plug", label: "Plug", chrome: { header: plugHeader } }, "plug");
  const plug = U.getUiShell("t-plug")!;

  const { seen, Probe } = counter();
  const el = dom.container();
  const root = createRoot(el as unknown as Element);
  const show = async (spec: import("@/plugins/uiShells").UiShellSpec | null) => {
    await act(async () => root.render(React.createElement(ComposedLayout, { spec }, React.createElement(Probe))));
    await flush();
  };

  await show(null);
  const node = el.querySelector("[data-probe]");
  assert.ok(node);
  assert.equal(P.classicShellId(), null);
  assert.equal(P.classicToolbarShown(), true);

  await show(desk);
  assert.equal(el.querySelector("[data-probe]"), node, "the layout kept its element");
  assert.ok(el.querySelector("[data-desk-header]"), "the shell's header is drawn");
  assert.equal(P.classicShellId(), "t-desk");
  assert.equal(P.classicToolbarShown(), false);
  assert.deepEqual(M.listShellModes().map((m) => m.id), ["model", "results"]);
  assert.ok(!listPanels().some((p) => p.id === "t:hidden-by-desk"), "its panel overrides");

  await show(plug);
  assert.equal(el.querySelector("[data-probe]"), node);
  assert.equal(el.querySelector("[data-desk-header]"), null, "the previous header is gone");
  assert.ok(el.querySelector("[data-plug-header]"));
  assert.deepEqual(M.listShellModes(), [], "and its modes");
  assert.ok(listPanels().some((p) => p.id === "t:hidden-by-desk"), "and its overrides");
  assert.ok(C.getCommand("plug:do"));
  assert.equal(O.overlaysAt("top-left").length, 1);

  await show(null);
  assert.equal(el.querySelector("[data-probe]"), node);
  assert.equal(C.getCommand("plug:do"), undefined, "what the plugin's shell registered while up is taken down");
  assert.ok(!S.listShortcuts().some((s) => s.id === "cmd:plug:do"));
  assert.equal(O.overlaysAt("top-left").length, 0, "its HUD too");
  assert.ok(C.getCommand("plug:feature"), "what the plugin had before stays");
  assert.equal(P.classicShellId(), null);
  assert.equal(P.classicToolbarShown(), true);

  await show(plug);
  assert.equal(O.overlaysAt("top-left").length, 1, "the module-level HUD comes back with its shell");
  assert.ok(C.getCommand("plug:do"));
  await show(null);
  assert.equal(O.overlaysAt("top-left").length, 0, "and goes again when it leaves again");
  assert.equal(C.getCommand("plug:do"), undefined);
  assert.deepEqual(seen, { mounts: 1, unmounts: 0 }, "the layout was never remounted");

  await act(async () => root.unmount());
  C.resetCommands();
  O.resetViewportOverlays();
  U.resetUiShells();
});

test("a mode switch keeps the layout mounted and the model and the selection as they were (the canvas, and its camera, is never remounted)", async () => {
  const desk: import("@/plugins/uiShells").UiShellSpec = {
    id: "t-modes",
    label: "Modes",
    layout: { modes: [{ id: "model", label: "Model" }, { id: "results", label: "Results", ownsSceneColor: true }] },
  };
  const { seen, Probe } = counter();
  const el = dom.container();
  const root = createRoot(el as unknown as Element);
  await act(async () => root.render(React.createElement(ComposedLayout, { spec: desk }, React.createElement(Probe))));
  await flush();
  const node = el.querySelector("[data-probe]");
  const loaded = new Set(["boxes.glb"]);
  useModelState.setState({ loadedSourceNames: loaded } as never);
  const before = useSelectedObjectStore.getState().selectedObjects;
  const selection = new Map(before as Map<unknown, unknown>);
  useSelectedObjectStore.setState({ selectedObjects: selection } as never);

  for (const mode of ["results", "model", "results", "model"]) {
    await act(async () => void M.setShellMode(mode));
    await flush();
    assert.equal(M.activeShellMode(), mode);
  }
  assert.equal(el.querySelector("[data-probe]"), node, "the layout (where the canvas lives) kept its element");
  assert.deepEqual(seen, { mounts: 1, unmounts: 0 });
  assert.equal(useModelState.getState().loadedSourceNames, loaded, "the loaded models are untouched");
  assert.equal(useSelectedObjectStore.getState().selectedObjects, selection, "the selection is untouched");

  await act(async () => root.unmount());
  M.setShellModes([]);
  P.setClassicShell(null);
});

test("after an in-place switch the page's pop-outs still boot into the shell it booted in", async () => {
  const { popoutUrl } = await import("@/layout/popout");
  U.resetUiShells();
  dom.setLocation("http://localhost/");
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", load: () => Promise.resolve({ default: () => null }) }, "core");
  U.registerUiShell({ id: "desk", label: "Desk" }, "core");
  try {
    assert.equal(U.activeUiShellId(), "core");
    assert.equal(new URL(popoutUrl("p:x", "http://localhost/")).searchParams.get("ui"), null, "not switched: nothing added");
    U.setUiShellSwitchHost((to) => U.canSwitchInPlace(U.activeUiShellId(), to));
    U.setActiveUiShell("desk");
    assert.equal(U.activeUiShellId(), "desk");
    assert.equal(new URL(popoutUrl("p:x", "http://localhost/")).searchParams.get("ui"), "core");
    assert.equal(new URL(popoutUrl("p:x", "http://localhost/?ui=desk")).searchParams.get("ui"), "desk", "an explicit ?ui= is kept");
  } finally {
    localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
    U.resetUiShells();
  }
});
