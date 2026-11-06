// The core actions the Desktop UI's menus offer beyond core's old keys: open a file from disk,
// select all / invert, the named camera views, the export dialog and About. Their pure parts
// (set math, view directions, the open plan, the About rows), their commands' registration and
// reasons, and the dialogs' markup.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { allShownRanges, invertedRanges, anyShownRange, selectionLabel } = await import("@/utils/mesh_select/selectionSets");
const { namedViewDirection, NAMED_VIEWS } = await import("@/components/viewer/sceneHelpers/namedViews");
const { localOpenPlan, localOpenAccept } = await import("@/utils/scene/handlers/localOpenPlan");
const { SUPPORTED_EXTS } = await import("@/utils/scene/handlers/sourceExts");
const C = await import("@/layout/commands");
const { CORE_COMMAND_IDS: ID, CAMERA_VIEWS, cameraViewCommandId } = await import("@/layout/coreCommandIds");
const { registerCoreCommands, CAMERA_VIEW_LABELS } = await import("@/layout/coreCommands");
const { currentCoreDialog, closeCoreDialog } = await import("@/layout/coreDialogs");
const { RESERVED_SHORTCUTS } = await import("@/layout/shortcuts");
const { desktopMenus, menuCommandIds } = await import("@/layout/desktop/desktopMenus");
const { aboutRows, AboutContent, ABOUT_LINKS } = await import("@/components/ui/AboutDialog");
const { ExportScopeTabs, modelRoots, planModelExport } = await import("@/components/export/ExportDialog");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const ExportDialog = (await import("@/components/export/ExportDialog")).default;

/** A batched mesh as the set math sees it. */
function mesh(ids: string[], hidden: string[] = [], visible = true, parent: { visible?: boolean } | null = null) {
  const h = new Set(hidden);
  return { visible, parent, drawRanges: new Map(ids.map((id) => [id, [0, 1]])), getHiddenRanges: () => h };
}

test("select all takes every shown range, never a hidden one or one of a hidden model", () => {
  const a = mesh(["1", "2", "3"], ["2"]);
  const b = mesh(["4"], [], false);
  const c = mesh(["5"], [], true, { visible: false });
  const d = mesh(["6", "7"]);
  assert.deepEqual(allShownRanges([a, b, c, d]).map(([m, id]) => [m === a ? "a" : "d", id]), [["a", "1"], ["a", "3"], ["d", "6"], ["d", "7"]]);
  assert.deepEqual(allShownRanges([]), []);
});

test("invert selects the shown ranges that were not selected, hidden ones staying out", () => {
  const a = mesh(["1", "2", "3", "4"], ["4"]);
  const b = mesh(["5", "6"]);
  const hiddenModel = mesh(["7"], [], false);
  // "4" is hidden AND selected: it is not kept, and not taken by the invert either.
  const sel = new Map([[a, new Set(["1", "4"])]]);
  const out = invertedRanges([a, b, hiddenModel], sel);
  assert.deepEqual(out.map(([m, id]) => [m === a ? "a" : "b", id]), [["a", "2"], ["a", "3"], ["b", "5"], ["b", "6"]]);
  // Inverting twice is the shown part of the first selection.
  const back = invertedRanges([a, b], new Map([[a, new Set(["2", "3"])], [b, new Set(["5", "6"])]]));
  assert.deepEqual(back.map(([, id]) => id), ["1"]);
  // Everything selected: the invert is empty.
  assert.deepEqual(invertedRanges([b], new Map([[b, new Set(["5", "6"])]])), []);
  assert.equal(selectionLabel("all", 1), "All: 1 element");
  assert.equal(selectionLabel("inverted", 3), "Inverted selection: 3 elements");
});

test("anyShownRange: whether a scene has something to select, duck-typed", () => {
  const scene = (...objs: unknown[]) => ({ traverse: (cb: (o: unknown) => void) => objs.forEach(cb) });
  assert.equal(anyShownRange(null), false);
  assert.equal(anyShownRange(scene({ type: "Group" })), false);
  assert.equal(anyShownRange(scene(mesh(["1"], ["1"]))), false, "all hidden");
  assert.equal(anyShownRange(scene(mesh(["1"], [], false))), false, "model switched off");
  assert.equal(anyShownRange(scene({}, mesh(["1", "2"], ["1"]))), true);
});

test("named views: unit directions in the viewer's Z-up world, and the same views Y-up", () => {
  assert.deepEqual([...NAMED_VIEWS], [...CAMERA_VIEWS]);
  const close = (a: number[], b: number[]) => a.every((v, i) => Math.abs(v - b[i]) < 1e-2);
  for (const v of NAMED_VIEWS) {
    for (const zUp of [true, false]) {
      const d = namedViewDirection(v, zUp);
      assert.ok(Math.abs(Math.hypot(...d) - 1) < 1e-12, `${v} is unit`);
    }
  }
  // Z up: front looks along +Y (the camera at -Y), top looks down.
  assert.deepEqual(namedViewDirection("front"), [0, -1, 0]);
  assert.deepEqual(namedViewDirection("right"), [1, 0, 0]);
  assert.ok(close(namedViewDirection("top"), [0, 0, 1]));
  assert.ok(close(namedViewDirection("bottom"), [0, 0, -1]));
  // Never exactly along the up axis: the picture's roll stays defined (back is up on screen).
  assert.ok(namedViewDirection("top")[1] < 0 && namedViewDirection("bottom")[1] > 0);
  const iso = namedViewDirection("isometric");
  assert.ok(iso[0] > 0 && iso[1] < 0 && iso[2] > 0, "from the front, right and above");
  // Opposites are opposite.
  for (const [a, b] of [["front", "back"], ["left", "right"]] as const) {
    assert.deepEqual(namedViewDirection(a).map((x) => -x + 0), namedViewDirection(b).map((x) => x + 0));
  }
  // Y up: (x, y, z) -> (x, z, -y): top is +Y, front is +Z.
  assert.ok(close(namedViewDirection("top", false), [0, 1, 0]));
  assert.deepEqual(namedViewDirection("front", false).map((x) => x + 0), [0, 0, 1]);
  assert.deepEqual(namedViewDirection("right", false).map((x) => x + 0), [1, 0, 0]);
});

test("open: GLB / glTF in any viewer; other formats only through a converting server", () => {
  const ws = { rest: false, convertEnabled: false, uploadExts: SUPPORTED_EXTS };
  const rest = { rest: true, convertEnabled: true, uploadExts: SUPPORTED_EXTS };
  const restNoConvert = { ...rest, convertEnabled: false };
  for (const env of [ws, rest, restNoConvert]) {
    assert.deepEqual(localOpenPlan("boxes.GLB", env), { kind: "scene" });
    assert.deepEqual(localOpenPlan("a.gltf", env), { kind: "scene" });
  }
  assert.deepEqual(localOpenPlan("plant.ifc", rest), { kind: "upload" });
  const wsIfc = localOpenPlan("plant.ifc", ws);
  assert.equal(wsIfc.kind, "refuse");
  assert.match((wsIfc as { reason: string }).reason, /not connected to one/);
  assert.match((localOpenPlan("plant.step", restNoConvert) as { reason: string }).reason, /does not convert/);
  assert.match((localOpenPlan("notes.txt", rest) as { reason: string }).reason, /A \.txt file cannot be opened/);
  assert.match((localOpenPlan("README", rest) as { reason: string }).reason, /without an extension/);
  assert.equal(localOpenAccept(ws), ".glb,.gltf");
  assert.match(localOpenAccept(rest), /^\.glb,\.gltf,\.ifc,/);
  assert.equal(localOpenAccept(restNoConvert), ".glb,.gltf");
});

test("the commands are registered, with their keys and their reasons", () => {
  registerCoreCommands();
  try {
    const get = (id: string) => {
      const c = C.getCommand(id);
      assert.ok(c, `${id} registered`);
      return c!;
    };
    assert.equal(get(ID.openFile).label, "Open…");
    assert.equal(C.commandChord(get(ID.openFile)), "Ctrl+O");
    assert.equal(C.commandChord(get(ID.selectAll)), "Ctrl+A");
    assert.ok(RESERVED_SHORTCUTS.includes("Ctrl+A") && RESERVED_SHORTCUTS.includes("Ctrl+O"), "plugins are refused the two chords");
    // No canvas here: the scene actions say so.
    assert.equal(get(ID.openFile).disabledReason?.(), "No 3D view");
    assert.equal(get(ID.selectAll).disabledReason?.(), "No 3D view");
    assert.equal(get(ID.invertSelection).disabledReason?.(), "No 3D view");
    for (const v of CAMERA_VIEWS) {
      const c = get(cameraViewCommandId(v));
      assert.equal(c.label, `${CAMERA_VIEW_LABELS[v]} view`);
      assert.equal(c.disabledReason?.(), "No 3D view");
    }
    // Not a REST viewer: the exports are the server's.
    assert.match(get(ID.exportSelection).disabledReason?.() ?? "", /not connected/);
    assert.match(get(ID.exportModel).disabledReason?.() ?? "", /not connected/);
    // About is always there, and opens core's dialog.
    assert.equal(get(ID.about).disabledReason, undefined);
    assert.equal(C.runCommand(ID.about), true);
    assert.deepEqual(currentCoreDialog(), { kind: "about" });
    closeCoreDialog();
    assert.equal(currentCoreDialog(), null);
  } finally {
    C.resetCommands();
  }
});

test("the Desktop UI's menus name them", () => {
  const menus = desktopMenus({ modes: [], workspaces: [], shells: [], themes: [] });
  const ids = menuCommandIds(menus);
  for (const id of [ID.openFile, ID.exportSelection, ID.exportModel, ID.selectAll, ID.invertSelection, ID.about, ...CAMERA_VIEWS.map(cameraViewCommandId)]) {
    assert.ok(ids.includes(id), id);
  }
  const file = menuCommandIds([menus.find((m) => m.label === "File")!]);
  assert.deepEqual(file.slice(0, 4), [ID.openFile, ID.uploadFile, ID.exportSelection, ID.exportModel]);
  const help = menuCommandIds([menus.find((m) => m.label === "Help")!]);
  assert.equal(help[help.length - 1], ID.about);
});

const baseAbout = {
  adapyVersion: "0.135.0",
  frontendSha: "abc1234",
  viewerImageTag: "",
  uniqueVersionId: 7,
  adapyBuildRef: "",
  rest: false,
  apiBase: "/api",
  websocketOff: false,
  websocketPort: 8765,
  shellLabel: "Desktop",
  server: null,
};

test("About: the build, the API versions, the UI and the connection; the server's tags on REST", async () => {
  const { VIEWER_CORE_API_VERSION } = await import("@/viewer-core");
  const { PLUGIN_API_VERSION } = await import("@/plugins/registry");
  const rows = new Map(aboutRows(baseAbout));
  assert.equal(rows.get("Viewer"), "0.135.0 (abc1234)");
  assert.equal(rows.get("Viewer core API"), VIEWER_CORE_API_VERSION);
  assert.equal(rows.get("Plugin API"), PLUGIN_API_VERSION);
  assert.equal(rows.get("User interface"), "Desktop");
  assert.equal(rows.get("Connection"), "Local (WebSocket, port 8765)");
  assert.equal(rows.has("Server"), false, "no server row off REST");
  assert.equal(new Map(aboutRows({ ...baseAbout, websocketOff: true })).get("Connection"), "None (no server)");
  const rest = { ...baseAbout, rest: true };
  assert.equal(new Map(aboutRows(rest)).get("Server"), "asking…");
  assert.equal(new Map(aboutRows({ ...rest, server: "error" })).get("Server"), "did not answer /config");
  const tagged = new Map(aboutRows({ ...rest, server: { viewerImageTag: "v1", workerImageTag: "w2", adapyBuildRef: null } }));
  assert.equal(tagged.get("Server image"), "v1");
  assert.equal(tagged.get("Worker image"), "w2");
  assert.equal(tagged.has("Built from"), false);
  assert.match(new Map(aboutRows({ ...rest, server: {} })).get("Server") ?? "", /no version reported/);
  assert.equal(new Map(aboutRows({ ...baseAbout, adapyVersion: "", frontendSha: "", uniqueVersionId: "" })).get("Viewer"), "development build");
});

test("About's markup: the rows and the documentation links, opened in a new tab", () => {
  const html = renderToStaticMarkup(React.createElement(AboutContent, { input: baseAbout }));
  assert.match(html, /<dt[^>]*>Viewer<\/dt><dd[^>]*>0\.135\.0 \(abc1234\)<\/dd>/);
  for (const l of ABOUT_LINKS) assert.ok(html.includes(`href="${l.href}"`), l.label);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
});

test("Export: the scope choice, the models it can export, and why a GLB cannot be", () => {
  const tabs = renderToStaticMarkup(React.createElement(ExportScopeTabs, { scope: "model", onScope: () => {} }));
  assert.match(tabs, /role="radiogroup"/);
  assert.match(tabs, /checked=""[^>]*\/>Whole model/);
  assert.deepEqual(modelRoots(null), { roots: [], indices: null });
  const glbRoot = { id: "m1", name: "boxes", source_name: "boxes.glb", children: [] };
  const ifcRoot = { id: "m2", name: "plant", top_name: "Plant", source_name: "plant.ifc", children: [{ id: "e1", name: "beam", children: [] }] };
  const { roots, indices } = modelRoots({ id: "__roots__", name: "", children: [glbRoot, ifcRoot] });
  assert.deepEqual(roots.map((r) => r.id), ["m1", "m2"]);
  // One model's tree without the container is wrapped in one.
  assert.deepEqual(modelRoots(ifcRoot).roots.map((r) => r.id), ["m2"]);
  assert.match((planModelExport(roots[0], indices, true, []) as { reason: string }).reason, /GLB/);
  const ifc = planModelExport(roots[1], indices, true, []);
  assert.ok(ifc.ok);
  assert.deepEqual(ifc.ok && ifc.request, { target: { kind: "file", sourceKey: "plant.ifc" }, element: null, path: [], label: "Plant" });
  assert.match((planModelExport(roots[1], indices, false, []) as { reason: string }).reason, /not connected/);
  assert.equal((planModelExport(null, null, true, []) as { reason: string }).reason, "No model is loaded.");
});

test("the Export dialog renders as a labelled modal, on either scope", () => {
  const render = (scope: "selection" | "model") =>
    renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(ExportDialog, { scope, onClose: () => {} })));
  const model = render("model");
  assert.match(model, /role="dialog"/);
  assert.match(model, /aria-modal="true"/);
  assert.match(model, /data-testid="export-dialog"/);
  assert.match(model, /No model is loaded\./);
  assert.match(render("selection"), /Nothing is selected/);
});
