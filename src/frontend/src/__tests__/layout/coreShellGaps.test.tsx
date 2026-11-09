// Generic shell features core carries (viewer-core 2.0): tree navigation and result view commands,
// property value labels, the split button, the status bar and page header, the component list panel,
// the builder's fuller toolbar, calculation options, UI freeze and the Desktop UI's use of them.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const C = await import("@/layout/commands");
const D = await import("@/layout/desktop/desktopShell");
const { desktopMenus, menuCommandIds } = await import("@/layout/desktop/desktopMenus");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { CORE_COMMAND_ICONS } = await import("@/layout/commandIcons");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const { splitButtonState } = await import("@/layout/SplitButton");
const { StatusBar, resolveStatusItems, compileLabel } = await import("@/layout/StatusBar");
const { PageHeader } = await import("@/layout/PageHeader");
const { CORE_STATUS_ITEM_IDS } = await import("@/layout/statusItems");
const { CORE_PANEL_IDS } = await import("@/layout/corePanelIds");
const { corePanelSpecs } = await import("@/layout/corePanels");
const { layoutStore, migrateLayoutPrefs, LAYOUT_PREFS_VERSION } = await import("@/layout/layoutStore");
const F = await import("@/layout/uiFreeze");
const T = await import("@/layout/ModeToolbars");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const { useColorStore } = await import("@/state/colorLegendStore");
const { useLabelsStore } = await import("@/state/labelsStore");
const { collectLabels, LabelKind } = (await import("@/utils/scene/fea/numberLabels")) as any;
const { valueLabelText } = await import("@/utils/scene/fea/elementValueTable");
const THREE = await import("three");
const V = await import("@/viewer-core");
const { readFileSync } = await import("node:fs");

const html = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));
const setup = () => {
  C.resetCommands();
  registerCoreCommands();
  useFeaAnimationStore.getState().reset();
};

test("the API versions moved together", () => {
  const reg = readFileSync(new URL("../../plugins/registry.ts", import.meta.url), "utf8");
  const plugin = /PLUGIN_API_VERSION = "([\d.]+)"/.exec(reg)![1];
  assert.equal(V.VIEWER_CORE_API_VERSION, plugin, "the two versions move together");
  const [maj, min] = plugin.split(".").map(Number);
  assert.ok(maj > 1 || min >= 16, "at least the release that added these");
});

const NEW_IDS = [
  ID.selectParent, ID.selectChild, ID.selectPrevSibling, ID.selectNextSibling,
  ID.colorLegend, ID.femConcepts, ID.dataDock, ID.beamSections, ID.warp, ID.labelsValues,
  ID.builderNew, ID.builderAddCell, ID.builderAddLoft, ID.builderAddOpening, ID.builderAddEquipment,
  ID.builderTranslate, ID.builderRotate, ID.builderResize,
  ID.builderRepTopology, ID.builderRepSimulation, ID.builderRepDetail, ID.builderSuperimpose, ID.builderSideBySide,
  ID.toggleUiFreeze,
];

test("every new command is registered, with an icon", () => {
  setup();
  for (const id of NEW_IDS) {
    assert.ok(C.getCommand(id), `${id} is registered`);
    assert.ok(CORE_COMMAND_ICONS[id], `${id} has an icon`);
  }
  assert.equal(ID.selectParent, "core:select-parent");
  C.resetCommands();
});

test("tree navigation shows the chord core binds, and is refused with nothing selected", () => {
  setup();
  for (const id of [ID.selectParent, ID.selectChild, ID.selectPrevSibling, ID.selectNextSibling]) {
    assert.equal(C.getCommand(id)!.shortcut, id);
    assert.equal(C.getCommand(id)!.disabledReason?.(), "Nothing selected");
  }
  C.resetCommands();
});

test("result view commands read the Simulation panel's state and say why they are off", () => {
  setup();
  for (const id of [ID.dataDock, ID.beamSections, ID.warp, ID.labelsValues]) {
    assert.equal(C.getCommand(id)!.disabledReason?.(), "No result is loaded", id);
  }
  assert.equal(C.getCommand(ID.femConcepts)!.disabledReason?.(), "No FE model is loaded");
  assert.equal(C.getCommand(ID.colorLegend)!.disabledReason?.(), "No result is loaded");
  const s = useFeaAnimationStore.getState();
  s.setSessionActive(true);
  s.setWarpEnabled(true);
  assert.equal(C.getCommand(ID.warp)!.checked?.(), true);
  assert.equal(C.getCommand(ID.warp)!.disabledReason?.(), null);
  assert.equal(C.getCommand(ID.colorLegend)!.disabledReason?.(), null);
  useColorStore.getState().setShowLegend(false);
  C.runCommand(ID.colorLegend);
  assert.equal(useColorStore.getState().showLegend, true);
  assert.equal(C.getCommand(ID.colorLegend)!.checked?.(), true);
  // No beam solids in the manifest: the reason names that.
  assert.equal(C.getCommand(ID.beamSections)!.disabledReason?.(), "This result has no beam sections");
  s.reset();
  useColorStore.getState().setShowLegend(false);
  C.resetCommands();
});

test("labelsValues toggles the store, and value labels outrank element numbers in a cell", () => {
  setup();
  useFeaAnimationStore.getState().setSessionActive(true);
  useLabelsStore.getState().setValues(false);
  C.runCommand(ID.labelsValues);
  assert.equal(useLabelsStore.getState().values, true);
  assert.equal(C.getCommand(ID.labelsValues)!.checked?.(), true);
  useLabelsStore.getState().setValues(false);
  useFeaAnimationStore.getState().reset();
  C.resetCommands();
  assert.equal(valueLabelText(0), "0");
  assert.equal(valueLabelText(12.3456789), "12.35");
  assert.equal(valueLabelText(1234567), "1.23e+6");
  assert.equal(valueLabelText(NaN), undefined);
});

test("collectLabels draws values for the elements that have one, thinned on the same grid", () => {
  // Two quads' worth of triangles as two draw ranges.
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0, 5, 0, 0, 6, 0, 0, 5, 1, 0], 3));
  geom.setIndex([0, 1, 2, 3, 4, 5]);
  const mesh: any = new THREE.Mesh(geom);
  mesh.drawRanges = new Map([["E1", [0, 3]], ["E2", [3, 3]]]);
  mesh.updateMatrixWorld(true);
  const camera = new THREE.OrthographicCamera(-8, 8, 8, -8, 0.1, 100);
  camera.position.set(0, 0, 10);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  const base = { nodes: false, elements: false, values: true, cap: 100, scanCap: 100, width: 800, height: 800 };
  const out = collectLabels(mesh, camera, { ...base, valueText: (n: number) => (n === 2 ? "7.5" : undefined) });
  assert.deepEqual(out.map((l: any) => [l.kind, l.text]), [["value", "7.5"]]);
  assert.deepEqual(collectLabels(mesh, camera, { ...base, values: false }), [], "off: nothing");
  void LabelKind;
});

test("a split button runs the picked type, opens the picker while none is picked, and never asks to cancel", () => {
  const o = { label: "Add opening", hasMenu: true };
  assert.equal(splitButtonState({ ...o, chosen: null }).action, "pick");
  assert.match(splitButtonState({ ...o, chosen: null }).tooltip, /choose a type/);
  assert.deepEqual(splitButtonState({ ...o, chosen: "Door" }), { action: "run", tooltip: "Add opening: Door" });
  assert.equal(splitButtonState({ ...o, chosen: null, pressed: true }).action, "run", "a second press puts the tool away");
  assert.equal(splitButtonState({ ...o, hasMenu: false, chosen: null }).action, "run");
  assert.equal(splitButtonState({ ...o }).action, "run", "no choice needed");
  assert.match(splitButtonState({ ...o, chosen: null, noun: "format" }).tooltip, /choose a format/);
});

test("the status bar composes core's items and the shell's own", () => {
  const items = resolveStatusItems(["connection", "nope" as never, "connection", { id: "mine", text: "Hello", end: true }]);
  assert.deepEqual(items.map((i) => i.id), ["connection", "mine"], "unknown dropped, repeated kept once");
  assert.deepEqual([...CORE_STATUS_ITEM_IDS], ["connection", "models", "selection", "mode", "undo", "compile", "version"]);
  const out = html(React.createElement(StatusBar, { items: ["models", "selection", { id: "mine", text: "Hello", end: true }] }));
  assert.match(out, /role="status"/);
  assert.match(out, /0 models loaded/);
  assert.match(out, /Nothing selected/);
  assert.match(out, /Hello/);
  assert.deepEqual(compileLabel({ status: "running" }, false), { text: "Compiling…", tone: "busy" });
  assert.equal(compileLabel({ status: "error" }, false)!.tone, "bad");
  assert.equal(compileLabel(null, false), null);
});

test("the Desktop UI draws its status bar from statusItems, with a spec item of its own", () => {
  const spec = D.desktopUiShellSpec();
  assert.equal(spec.chrome!.footer, undefined);
  const ids = resolveStatusItems(spec.chrome!.statusItems!).map((i) => i.id);
  assert.ok(ids.includes("connection") && ids.includes("compile") && ids.includes("ui"));
});

test("the page header has slots for a scope picker and the shell's controls", () => {
  const out = renderToStaticMarkup(
    React.createElement(PageHeader, { title: "Convert files", backHref: "/", scopePicker: React.createElement("select", { id: "scope" }), trailing: React.createElement("i", { id: "extra" }) }),
  );
  assert.match(out, /Convert files/);
  assert.match(out, /id="scope"/);
  assert.match(out, /id="extra"/);
  assert.match(out, /Back to the viewer/);
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(PageHeader, { title: "x", backHref: "/" })), /data-page-header-scope/);
});

test("the component list is a core panel of its own, available with a procedural model", () => {
  const spec = corePanelSpecs().find((p) => p.id === CORE_PANEL_IDS.componentList)!;
  assert.ok(spec, "registered");
  assert.equal(spec.binding !== undefined && typeof spec.content, "function");
  const before = useCellBuilderStore.getState().componentListVisible;
  spec.binding!.setOpen(true);
  assert.equal(useCellBuilderStore.getState().componentListVisible, true);
  spec.binding!.setOpen(false);
  assert.equal(useCellBuilderStore.getState().componentListVisible, before);
  assert.ok(menuCommandIds(desktopMenus({ modes: [{ id: "build" }], workspaces: [], shells: [], themes: [] })).includes(`panel:${CORE_PANEL_IDS.componentList}`));
});

test("the Build mode is always offered in the Desktop UI, its menu has the first action", () => {
  const spec = D.desktopUiShellSpec();
  const build = spec.layout!.modes!.find((m) => m.id === "build")!;
  assert.equal(build.requires, undefined);
  const menus = desktopMenus({ modes: [{ id: "build" }], workspaces: [], shells: [], themes: [] });
  const ids = menuCommandIds(menus);
  for (const id of [ID.builderNew, ID.builderAddCell, ID.builderTranslate, ID.builderRepSimulation, ID.toggleUiFreeze, ID.labelsValues, ID.selectParent, ID.dataDock]) {
    assert.ok(ids.includes(id), `${id} is in a menu`);
  }
  assert.ok(menuCommandIds(menus).every((id) => id.includes(":")), "ids only");
  assert.ok(spec.layout!.modes!.find((m) => m.id === "build")!.toolbars!.includes("build"));
});

test("the builder's commands grey with their reason with no model open", () => {
  setup();
  for (const id of [ID.builderAddCell, ID.builderAddLoft, ID.builderTranslate, ID.builderRepSimulation]) {
    assert.ok(C.getCommand(id)!.disabledReason?.(), `${id} has a reason`);
  }
  const out = html(React.createElement(T.BuilderToolbar));
  assert.match(out, /data-command="core:builder-new"/);
  assert.match(out, /No procedural model/);
  C.resetCommands();
});

test("the facade publishes the glyphs, the menu and modal, and the theme entry", () => {
  const layout = readFileSync(new URL("../../viewer-core/layout.ts", import.meta.url), "utf8");
  for (const name of ["ChevronRightIcon", "PositionedMenu", "ModalFrame", "SplitButton", "StatusBar", "PageHeader", "ComputeOptionsBody", "ComponentListBody", "BuilderPlacementTools"]) {
    assert.match(layout, new RegExp(`\\b${name}\\b`), name);
  }
  const theme = readFileSync(new URL("../../viewer-core/theme.ts", import.meta.url), "utf8");
  assert.match(theme, /useThemeStore/);
  assert.doesNotMatch(theme, /from "@\/(state\/AdaViewerContext|runtime)/);
  const api = readFileSync(new URL("../../services/api/fea.ts", import.meta.url), "utf8");
  assert.match(api, /model_info\?: FeaModelInfo/);
});

test("the Options panel has a calculation section over the compute store", () => {
  const src = readFileSync(new URL("../../components/OptionsComponent.tsx", import.meta.url), "utf8");
  assert.match(src, /"compute"/);
  assert.match(src, /ComputeOptions/);
});

// --- UI freeze ---

const resetPrefs = () => layoutStore.setState({ prefs: { v: LAYOUT_PREFS_VERSION, docs: {} } });

test("UI freeze is kept per shell, migrated safely, and shell options decide the default", () => {
  resetPrefs();
  F.setUiFreezeShell(null, undefined);
  assert.equal(F.uiFrozen(), false, "off until chosen");
  F.setUiFrozen(true);
  assert.deepEqual(layoutStore.getState().prefs.uiFreezeByShell, { core: true });
  assert.equal(F.uiFrozen(), true);
  F.setUiFreezeShell("desk", undefined);
  assert.equal(F.uiFrozen(), false, "another shell has its own");
  F.setUiFreezeShell("desk", "default-on");
  assert.equal(F.uiFrozen(), true, "starts frozen");
  F.setUiFrozen(false);
  assert.equal(F.uiFrozen(), false, "the user's choice wins over the default");
  F.setUiFreezeShell("desk", false);
  assert.equal(F.uiFreezeOffered(), false);
  assert.equal(F.uiFrozen(), false, "opted out: never frozen");
  F.setUiFrozen(true);
  assert.equal(layoutStore.getState().prefs.uiFreezeByShell!.desk, false, "ignored where opted out");
  const p = migrateLayoutPrefs({ v: LAYOUT_PREFS_VERSION, docs: {}, uiFreezeByShell: { a: true, b: "no" } });
  assert.deepEqual(p.uiFreezeByShell, { a: true });
  F.setUiFreezeShell(null, undefined);
  resetPrefs();
});

test("the freeze command is offered unless the shell opts out, and toggles the setting", () => {
  resetPrefs();
  F.setUiFreezeShell(null, undefined);
  setup();
  const cmd = C.getCommand(ID.toggleUiFreeze)!;
  assert.equal(cmd.when!(), true);
  assert.equal(cmd.checked!(), false);
  C.runCommand(ID.toggleUiFreeze);
  assert.equal(cmd.checked!(), true);
  F.setUiFreezeShell("x", false);
  assert.equal(cmd.when!(), false);
  F.setUiFreezeShell(null, undefined);
  C.resetCommands();
  resetPrefs();
});

test("a frozen layout has no drop targets", async () => {
  const { moveDockDrag, endDockDragAt, dockDragStore } = await import("@/layout/dockDrag");
  resetPrefs();
  F.setUiFreezeShell(null, undefined);
  const hit = () => ({ zone: "z", index: null });
  F.setUiFrozen(true);
  moveDockDrag("p", 1, 1, hit);
  assert.equal(dockDragStore.getState().panel, null, "the drag never starts");
  assert.equal(endDockDragAt(1, 1, hit), null);
  F.setUiFrozen(false);
  moveDockDrag("p", 1, 1, hit);
  assert.equal(dockDragStore.getState().panel, "p");
  endDockDragAt(1, 1, hit);
  resetPrefs();
});

test("frozen headers lose the docking buttons and keep close", async () => {
  const { WindowDockProvider, PanelHeaderActionsProvider, PanelHeaderActions, WindowDockActions } = await import("@/components/ui/PanelFrame");
  resetPrefs();
  F.setUiFreezeShell(null, undefined);
  const actions = [{ id: "float", label: "Float in a window", run: () => {} }];
  const el = () => (
    <PanelHeaderActionsProvider actions={actions}>
      <WindowDockProvider value={{ panelId: "p", actions, onDragMove: () => {}, onDragEnd: () => false }}>
        <PanelHeaderActions />
        <WindowDockActions />
      </WindowDockProvider>
    </PanelHeaderActionsProvider>
  );
  assert.match(renderToStaticMarkup(el()), /Float in a window/);
  // Server rendering reads the store's initial state, so the frozen markup is checked in the browser
  // smoke; here, that every header path asks the setting.
  const frame = readFileSync(new URL("../../components/ui/PanelFrame.tsx", import.meta.url), "utf8");
  for (const fn of ["usePanelHeaderActions", "WindowDockActions", "WindowFrame", "PanelFrame"]) {
    const body = frame.slice(frame.indexOf(`function ${fn}`));
    assert.match(body.slice(0, 700), /useUiFrozen\(\)/, fn);
  }
  const grid = readFileSync(new URL("../../layout/presets/classicGrid.tsx", import.meta.url), "utf8");
  assert.ok((grid.match(/useUiFrozen\(\)/g) ?? []).length >= 4, "tabs, drop targets and both group kinds");
  resetPrefs();
});

test("the Desktop UI's menus offer the freeze, and Options shows it", () => {
  const ids = menuCommandIds(desktopMenus({ modes: [], workspaces: [], shells: [], themes: [] }));
  assert.ok(ids.includes(ID.toggleUiFreeze));
  const opt = readFileSync(new URL("../../components/options/LayoutOptions.tsx", import.meta.url), "utf8");
  assert.match(opt, /data-testid="ui-freeze"/);
});

test("the mode toolbars live in a region core owns: it fills the room, and the header keeps the switcher in one place", async () => {
  const { overflows, ModeToolbarRegion } = await import("@/layout/ModeToolbarRegion");
  assert.equal(overflows({ scrollWidth: 300, clientWidth: 200 }), true);
  assert.equal(overflows({ scrollWidth: 200, clientWidth: 200 }), false);
  const out = renderToStaticMarkup(React.createElement(ModeToolbarRegion, null, React.createElement("b", { id: "t" })));
  assert.match(out, /data-mode-toolbar-region/);
  assert.match(out, /flex min-w-0 flex-1/);
  assert.match(out, /id="t"/);
  assert.doesNotMatch(out, /data-mode-toolbar-more/, "no overflow button while it fits");
  const header = readFileSync(new URL("../../layout/desktop/DesktopHeader.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(header, /mx-auto/, "nothing centred: the switcher does not move with the toolbars");
  assert.match(header, /ShellModeSwitcher[^>]*shrink-0/);
});