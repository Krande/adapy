// Topology > Input's properties, core's part: the builder's selection is three stacked sections
// (the item at 70, its space at 72, its structure at 74), a plugin's section slots between them;
// the grid and snapping options are a Settings page a command opens; a shell hears of a failed
// compile.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { ObjectInfoBody, CORE_PROPERTY_PROVIDERS } = await import("@/components/info_box_selected_object/ObjectInfoBoxComponent");
const { providersFor } = await import("@/plugins/propertyProviders");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { SETTINGS_SECTIONS } = await import("@/components/settings/settingsSections");
const { SettingsBody } = await import("@/components/settings/SettingsPanel");
const { setSettingsHost } = await import("@/components/settings/settingsTab");
const { CORE_COMMAND_IDS } = await import("@/layout/coreCommandIds");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const { runCommand } = await import("@/layout/commands");
const { onCompileFailed, selectCompileFailure } = await import("@/state/cellbuilder/compileFailures");
type Snapshot = import("@/plugins/propertyProviders").SelectionSnapshot;

const st = () => useCellBuilderStore.getState();
// A server render reads the stores' initial state: render the builder store as it is now, then
// put its initial state back.
const pristine = { ...useCellBuilderStore.getInitialState() };
const inViewer = (el: React.ReactElement) => {
  Object.assign(useCellBuilderStore.getInitialState(), st());
  try {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));
  } finally {
    Object.assign(useCellBuilderStore.getInitialState(), pristine);
  }
};

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "room", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, STRUCTURE_NAME: "Upper" }],
  equipments: [{ NAME: "pump", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1, SPACE_NAME: "room" }],
  groups: [{ name: "Upper", blueprint: "plate" }],
} as unknown as ProceduralDoc;

const none: Snapshot = { kind: "none", name: null, meshName: null, count: 0, hasEntities: true, cellBuilderActive: true };

test("the builder's sections stack at 70, 72 and 74, and a plugin's slots between them", () => {
  const plugins = [
    { id: "after-item", order: 71, match: () => true, render: () => null },
    { id: "after-space", order: 73, match: () => true, render: () => null },
  ];
  const ids = (sel: Snapshot) =>
    providersFor(sel, CORE_PROPERTY_PROVIDERS, plugins)
      .map((p) => p.id)
      .filter((id) => id.startsWith("cellbuilder-") || id.startsWith("after-"));
  const ref: Snapshot = { ...none, kind: "ref", name: "r", count: 1, refOwner: "p", refKey: "k", structure: "Upper", space: "room" };
  assert.deepEqual(ids(ref), ["cellbuilder-item", "after-item", "cellbuilder-space", "after-space", "cellbuilder-structure"]);
  assert.deepEqual(ids({ ...ref, kind: "cell", space: undefined }), ["cellbuilder-item", "after-item", "after-space", "cellbuilder-structure"]);
  assert.deepEqual(ids({ ...none, structure: "Upper" }), ["after-item", "after-space", "cellbuilder-structure"], "the active structure, nothing picked");
  assert.deepEqual(ids(none), ["after-item", "after-space"]);
});

test("a picked equipment shows its own section, its space's and its structure's, in that order", () => {
  st().open("m", "Model", 1, doc);
  try {
    const pump = Object.values(st().cells).find((c) => c.name === "pump")!;
    st().setSelection({ kind: "cell", cellId: pump.id });
    const html = inViewer(React.createElement(ObjectInfoBody));
    const at = (s: string) => html.indexOf(`data-builder-section="${s}"`);
    assert.ok(at("item") >= 0 && at("item") < at("space") && at("space") < at("structure"), html.slice(0, 200));
    assert.match(html, /Space room/);
    assert.match(html, /Structure Upper/);
    // The space itself picked: its own section, no second one for it.
    const room = Object.values(st().cells).find((c) => c.name === "room")!;
    st().setSelection({ kind: "cell", cellId: room.id });
    const own = inViewer(React.createElement(ObjectInfoBody));
    assert.equal(own.includes('data-builder-section="space"'), false);
    assert.ok(own.includes('data-builder-section="structure"'));
    // Nothing picked: the active structure still has its section.
    st().setSelection(null);
    st().setActiveGroup("Upper");
    const idle = inViewer(React.createElement(ObjectInfoBody));
    assert.ok(idle.includes('data-builder-section="structure"'));
    assert.equal(idle.includes('data-builder-section="item"'), false);
  } finally {
    st().close();
  }
});

test("a picked ref: its name, kind, box fields when it may move, include; its space and structure", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "p",
    project: () => [
      { key: "k", name: "marker", kindLabel: "Marker", origin: [1, 1, 3], size: [1, 1, 0.02], space: "room", look: "face", edit: { move: true } },
    ],
    write: () => true,
  });
  try {
    st().setSelection({ kind: "ref", refId: "p:k" });
    const html = inViewer(React.createElement(ObjectInfoBody));
    assert.match(html, /data-builder-ref="p:k"/);
    assert.match(html, /Marker/);
    assert.match(html, /aria-label="origin X"/, "may move: its origin is editable");
    assert.doesNotMatch(html, /aria-label="size X"/, "may not resize: no size fields");
    assert.match(html, /Include in build/);
    assert.match(html, /Space room/);
    assert.match(html, /Structure Upper/, "its structure is its space's");
  } finally {
    off();
    st().close();
  }
});

test("Settings has a Grid & snapping page, and core:builder-grid-settings opens it", () => {
  const page = SETTINGS_SECTIONS.find((s) => s.id === "grid");
  assert.ok(page, "registered");
  assert.equal(page!.title, "Grid & snapping");
  assert.equal(page!.available(), true);
  const html = inViewer(React.createElement(SettingsBody, { initialTab: "grid" }));
  for (const t of ["Grid step", "Snap distance", "Select", "Vertex snap", "Move equipment with cell", "Drag faces to resize"]) {
    assert.match(html, new RegExp(t), t);
  }
  assert.equal(CORE_COMMAND_IDS.builderGridSettings, "core:builder-grid-settings");
  registerCoreCommands();
  const asked: (string | null)[] = [];
  const undo = setSettingsHost((tab) => asked.push(tab));
  try {
    assert.equal(runCommand(CORE_COMMAND_IDS.builderGridSettings), true);
    assert.deepEqual(asked, ["grid"]);
  } finally {
    undo();
  }
});

test("a failed compile is told once, and the selector has it", () => {
  const heard: string[] = [];
  const off = onCompileFailed((f) => heard.push(f.error ?? ""));
  try {
    useCellBuilderStore.setState({ compileJob: { jobId: "j", derivedKey: "d", status: "running" } });
    assert.equal(selectCompileFailure(st()), null);
    useCellBuilderStore.setState({ compileJob: { jobId: "j", derivedKey: "d", status: "error", error: "boom" } });
    useCellBuilderStore.setState({ gridStep: 0.3 });
    assert.deepEqual(heard, ["boom"], "once, not on a later change");
    assert.equal(selectCompileFailure(st())?.error, "boom");
  } finally {
    off();
    useCellBuilderStore.setState({ compileJob: null });
  }
});

test("core's Model panel lists a ref as a row of its own, under its space, with what it allows", async () => {
  const { ComponentList } = await import("@/components/viewer/cellbuilder/panel/ComponentList");
  const { useModelPanelViewStore } = await import("@/state/modelPanelViewStore");
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "p",
    project: () => [
      { key: "k", name: "marker", kindLabel: "Marker", origin: [1, 1, 3], size: [1, 1, 0.02], space: "room", include: false, edit: { remove: true } },
    ],
    remove: () => {},
  });
  const view = useModelPanelViewStore.getInitialState().view;
  Object.assign(useModelPanelViewStore.getInitialState(), { view: "tree" });
  try {
    const html = inViewer(React.createElement(ComponentList));
    const room = html.indexOf('data-component-group="cell:');
    const row = html.indexOf('data-ref-row="p:k"');
    assert.ok(room >= 0 && row > room, "under the space's row");
    assert.match(html, /aria-label="Include marker in build"/);
    assert.match(html, /aria-label="Delete marker"/);
    assert.match(html, /data-ref-row="p:k" data-include-state="excluded"/);
  } finally {
    Object.assign(useModelPanelViewStore.getInitialState(), { view });
    off();
    st().close();
  }
});
