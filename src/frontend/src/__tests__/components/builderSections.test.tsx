// Topology > Input's properties, core's part: the builder's pick is one layout (the item at 70:
// its space and structure as a parents line first, then its badge and name, its geometry and its
// groups), a structure on its own is the section at 74, and a plugin's section slots between them;
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

test("the builder's sections are the item at 70 and a lone structure at 74, and a plugin's slots between them", () => {
  const plugins = [
    { id: "after-item", order: 71, match: () => true, render: () => null },
    { id: "after-space", order: 73, match: () => true, render: () => null },
  ];
  const ids = (sel: Snapshot) =>
    providersFor(sel, CORE_PROPERTY_PROVIDERS, plugins)
      .map((p) => p.id)
      .filter((id) => id.startsWith("cellbuilder-") || id.startsWith("after-"));
  const ref: Snapshot = { ...none, kind: "ref", name: "r", count: 1, refOwner: "p", refKey: "k", structure: "Upper", space: "room" };
  // A pick's space and structure are its parents line, inside the item: no sections of their own.
  assert.deepEqual(ids(ref), ["cellbuilder-item", "after-item", "after-space"]);
  assert.deepEqual(ids({ ...ref, kind: "cell", space: undefined }), ["cellbuilder-item", "after-item", "after-space"]);
  assert.deepEqual(ids({ ...none, structure: "Upper" }), ["after-item", "after-space", "cellbuilder-structure"], "the active structure, nothing picked");
  assert.deepEqual(ids(none), ["after-item", "after-space"]);
});

/** Where `marker` first appears in `html`, or -1. */
const at = (html: string, marker: string) => html.indexOf(marker);

test("a picked equipment: where it is first (its space and structure), then its badge and name, its geometry, its groups", () => {
  st().open("m", "Model", 1, doc);
  try {
    const pump = Object.values(st().cells).find((c) => c.name === "pump")!;
    st().setSelection({ kind: "cell", cellId: pump.id });
    const html = inViewer(React.createElement(ObjectInfoBody));
    assert.ok(at(html, 'data-builder-section="item"') >= 0);
    assert.equal(at(html, 'data-builder-section="space"'), -1, "the space is the parents line, not a section");
    assert.equal(at(html, 'data-builder-section="structure"'), -1, "so is the structure");
    const order = [
      "data-property-parents",
      "data-property-header",
      'data-property-group="Geometry"', // Position, Size, Rotation° and COG, one grid
      'data-property-group="Placement"', // Location only now
      'data-property-group="Mass"',
      'data-property-group="Systems"',
    ];
    const where = order.map((m) => at(html, m));
    assert.ok(where.every((w, i) => w >= 0 && (i === 0 || w > where[i - 1])), `in order: ${where}`);
    assert.match(html, /data-property-parents="">.*title="Select space room">room<\/button>.*title="Select structure Upper">Upper<\/button>.*\[plate\]/);
    assert.match(html, /aria-label="Name of [^"]* pump"/, "renamed in place");
    assert.match(html, /aria-label="Position X"/);
    // Rotation° and COG now share the Geometry grid, not their own inline row/style.
    assert.match(html, /data-property-group="Geometry">[\s\S]*?Rotation°[\s\S]*?aria-label="Rotation° Z"[\s\S]*?COG[\s\S]*?aria-label="COG Z"/);
    assert.doesNotMatch(html, /aria-label="Rotation Z \(ROT_Z\)"/, "the old separate Rotation row is gone");
    // What the Model panel and the toolbar do is not repeated here.
    assert.doesNotMatch(html, />(Move|Rotate|Resize)<\/button>/, "no gizmo buttons");
    assert.doesNotMatch(html, /Include in build/, "no include toggle");
    assert.doesNotMatch(html, /<span class="font-semibold">Properties<\/span>/, "no nested Properties");
    // The space itself picked: no space in its parents line, its structure still there.
    const room = Object.values(st().cells).find((c) => c.name === "room")!;
    st().setSelection({ kind: "cell", cellId: room.id });
    const own = inViewer(React.createElement(ObjectInfoBody));
    assert.doesNotMatch(own, /title="Select space room"/);
    assert.match(own, /title="Select structure Upper"/);
    for (const g of ["Grid", "Beams", "Enclosed", "General"]) assert.match(own, new RegExp(`data-property-group="${g}"`), g);
    assert.match(own, /data-field="Create">[\s\S]*?aria-label="X \(GRID_X_CREATE\)"[\s\S]*?aria-label="Z \(GRID_Z_CREATE\)"/, "Grid X / Y / Z on one row");
    // Named views, in the viewer's own Alt+1..6 order (Top/Bottom/Front/Back/Left/Right).
    assert.match(
      own,
      /data-field="Exclude side">[\s\S]*?Exclude Top \(SE1\)[\s\S]*?Exclude Bottom \(SE0\)[\s\S]*?Exclude Front \(SE2\)[\s\S]*?Exclude Back \(SE3\)[\s\S]*?Exclude Left \(SE4\)[\s\S]*?Exclude Right \(SE5\)/,
      "Top Bottom Front Back Left Right, in that order",
    );
    // FUNCTION, GIRDER_AS_PLATE and DESCRIPTION, missing from the form before.
    assert.match(own, /data-field="Function">[\s\S]*?aria-label="Function \(FUNCTION\)"/);
    assert.match(own, /data-field="Girder as plate">[\s\S]*?aria-label="Girder as plate \(GIRDER_AS_PLATE\)"/);
    assert.match(own, /data-field="Description">[\s\S]*?aria-label="Description \(DESCRIPTION\)"/);
    // The built-in engine: Room / plated walls still offered.
    assert.match(own, /data-field="Room">/);
    assert.equal(at(own, 'data-builder-section="structure"'), -1);
    // Nothing picked: the active structure has its own view.
    st().setSelection(null);
    st().setActiveGroup("Upper");
    const idle = inViewer(React.createElement(ObjectInfoBody));
    assert.ok(idle.includes('data-builder-section="structure"'));
    assert.equal(idle.includes('data-builder-section="item"'), false);
    assert.match(idle, /aria-label="Name of structure Upper"/);
    assert.match(idle, /data-field="Blueprint"/);
    assert.match(idle, /aria-label="Include structure Upper in build"/);
  } finally {
    st().close();
  }
});

test("a field's label carries the engine's help for its column as a tooltip, the column named first", () => {
  st().open("m", "Model", 1, doc);
  const engines = st().engines;
  useCellBuilderStore.setState({
    engines: [{ id: "x", slug: st().selectedEngine, name: "x", description: "", revision: 0, field_help: { spaces: { GRID_X_CREATE: "Use in building X grid" } } }],
  });
  try {
    const room = Object.values(st().cells).find((c) => c.name === "room")!;
    st().setSelection({ kind: "cell", cellId: room.id });
    const html = inViewer(React.createElement(ObjectInfoBody));
    assert.match(html, /data-field="Create"><span class="[^"]*cursor-help[^"]*" title="GRID_X_CREATE — Use in building X grid">Create<\/span>/);
    assert.match(html, /<label class="inline-flex items-center gap-0.5" title="GRID_X_CREATE — Use in building X grid"><input type="checkbox"[^>]*aria-label="X \(GRID_X_CREATE\)"/);
  } finally {
    useCellBuilderStore.setState({ engines });
    st().close();
  }
});

test("builderSummary false leaves the summary out for a builder pick", () => {
  st().open("m", "Model", 1, doc);
  try {
    const pump = Object.values(st().cells).find((c) => c.name === "pump")!;
    st().setSelection({ kind: "cell", cellId: pump.id });
    assert.match(inViewer(React.createElement(ObjectInfoBody)), /aria-label="Unhide all"/, "classic: as before");
    const html = inViewer(React.createElement(ObjectInfoBody, { builderSummary: false }));
    assert.doesNotMatch(html, /aria-label="Unhide all"|aria-label="Hide selected"|Add mode/);
    assert.match(html, /data-builder-section="item"/);
  } finally {
    st().close();
  }
});

test("a picked ref: its kind and name, its box editable where it may move, its space and structure; a provider's own form replaces it", () => {
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
    assert.match(html, /data-property-header=""><span[^>]*>Marker<\/span>/, "its kind is the badge");
    assert.match(html, /<input[^>]*aria-label="Position X"/, "may move: its position is editable");
    assert.doesNotMatch(html, /<input[^>]*aria-label="Size X"/, "may not resize: its size is shown, not editable");
    assert.match(html, /title="Select space room"/);
    assert.match(html, /title="Select structure Upper"/, "its structure is its space's");
  } finally {
    off();
    st().close();
  }
  // `edit.form`: the provider draws it, core draws nothing for the item.
  st().open("m", "Model", 1, doc);
  const off2 = registerBuilderRefs({
    owner: "p",
    project: () => [{ key: "k", name: "marker", kindLabel: "Marker", origin: [1, 1, 3], size: [1, 1, 0.02], space: "room", edit: { form: true } }],
  });
  try {
    st().setSelection({ kind: "ref", refId: "p:k" });
    const html = inViewer(React.createElement(ObjectInfoBody));
    assert.doesNotMatch(html, /data-builder-ref="p:k"/);
    assert.doesNotMatch(html, /data-builder-section="item"/);
  } finally {
    off2();
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

test("whether a builder pick is the panel's is the active mode's declared builder presence", async () => {
  const { setBuilderVisibilityHost } = await import("@/state/cellbuilder/builderVisibility");
  const M = await import("@/layout/shellModes");
  st().open("m", "Model", 1, doc);
  let shown = false;
  const release = setBuilderVisibilityHost({
    cellsShown: () => shown,
    selected: () => [],
    all: () => [],
    hide: () => {},
    unhide: () => {},
    unhideAll: () => {},
    showComplement: () => {},
    anyHidden: () => false,
  });
  const panel = () => inViewer(React.createElement(ObjectInfoBody));
  const isPick = (html: string) => /data-builder-section="item"/.test(html);
  try {
    const pump = Object.values(st().cells).find((c) => c.name === "pump")!;
    st().setSelection({ kind: "cell", cellId: pump.id });

    // Core's own UI has no modes: the pick is the panel's, whatever the builder draws (a compile
    // shows its model and hides the cells).
    M.setShellModes([]);
    assert.ok(isPick(panel()), "no modes: the pick stays with the cells hidden");

    // "away": the model is open behind the mode -- no section, no summary naming it.
    M.setShellModes([{ id: "away", label: "Away", order: 0, behaviour: { builder: "away" } }], "away");
    shown = true;
    const away = panel();
    assert.doesNotMatch(away, /data-builder-section/, "no builder section");
    assert.doesNotMatch(away, />pump</, "no summary naming it");
    assert.doesNotMatch(away, /Add mode/);

    // "compare" (and a mode that declares nothing): the pick follows whether the cells are drawn.
    for (const behaviour of [{ builder: "compare" as const }, undefined]) {
      M.setShellModes([{ id: "cmp", label: "Compare", order: 0, ...(behaviour ? { behaviour } : {}) }], "cmp");
      shown = false;
      assert.ok(!isPick(panel()), "cells hidden: not the panel's");
      shown = true;
      assert.ok(isPick(panel()), "cells drawn: the panel's");
    }

    // "edit": the model is worked on here; the pick is the panel's with the cells hidden too.
    M.setShellModes([{ id: "edit", label: "Edit", order: 0, behaviour: { builder: "edit" } }], "edit");
    shown = false;
    assert.ok(isPick(panel()), "edit: the pick stays after a compile hides the cells");
  } finally {
    M.setShellModes([]);
    release();
    st().close();
  }
});
