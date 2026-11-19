// Every way of emptying the scene goes through ONE function, `clearScene` (utils/scene/handlers/
// clear_scene.ts), so whatever is added to the teardown applies to every trigger. The storage
// browser's Clear left the connection builder's annotations -- drawn through the viewport's overlay
// and billboard layers, which are not a model's -- on screen; they now go in that one teardown, and
// with their own model when it is unloaded alone.

import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { test } from "node:test";

import type { DevBuildResult } from "@/services/api/devConnections";
import type { ViewportApi } from "@/viewport/api/viewportApi";

// Answers vite's `?worker` imports (the FEA streaming teardown imports one).
await import("../fixtures/panelRealm");

const { clearScene } = await import("@/utils/scene/handlers/clear_scene");
const { renderConnectionOverlay } = await import("@/utils/scene/connectionOverlay");
const { markWelds } = await import("@/utils/scene/connectionWeldMarks");
const { highlightObjects } = await import("@/plugins/sceneHighlights");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { useModelState } = await import("@/state/modelState");
const { useTreeViewStore } = await import("@/state/treeViewStore");

const PREVIEW = "connection-preview:box_to_box#7";
const OTHER = "model.ifc";

const result = {
  properties: {
    members: [{ name: "beam1", role: "landing", type: "BEAM", section: { name: "IPE300" }, axis: [[0, 0, 0], [1, 0, 0]] }],
    welds: [{ name: "w1", weld_type: "fillet", throat: 0.005, at: [0, 0, 1], joins_its_members: true }],
  },
} as unknown as DevBuildResult;
const TOGGLES = { axes: true, welds: true, weldSymbols: false, weldDetails: false };

/** A viewport that counts what is drawn in its layers and what it is asked to mark. */
function countingViewport() {
  const live = { lines: 0, labels: 0, cleared: 0, marks: -1 };
  const viewport = {
    kind: "native",
    sourceOrigin: (name: string) => (useModelState.getState().loadedSourceNames.has(name) ? [0, 0, 0] : null),
    overlays: () => ({ addLines: () => ++live.lines, dispose: () => void (live.lines = 0) }),
    billboards: () => ({ add: () => ++live.labels, update: () => {}, dispose: () => void (live.labels = 0) }),
    setObjectMarks: (marks: Map<string, Map<string, string>>) =>
      void (live.marks = [...marks.values()].reduce((n, ids) => n + ids.size, 0)),
    clearModels: () => void live.cleared++,
    hide: () => {},
    show: () => {},
  } as unknown as ViewportApi;
  return { viewport, live };
}

/** The preview loaded, with its tree row (the welds are found by name in it), annotated and marked. */
function buildPreview(live: { lines: number; labels: number }) {
  const ms = useModelState.getState();
  ms.registerLoadedSourceName(OTHER);
  ms.registerLoadedSourceName(PREVIEW);
  useTreeViewStore.getState().setTreeData({
    id: "__roots__",
    name: "",
    children: [{ id: "r", name: "preview", source_name: PREVIEW, model_key: "m7", children: [{ id: "w", name: "w1", rangeId: 3 }] }],
  } as never);
  assert.equal(renderConnectionOverlay(PREVIEW, result, TOGGLES), 2);
  assert.equal(markWelds(PREVIEW, ["w1"], { colour: "#ff8800", hidden: false }), 1);
  assert.ok(live.lines > 0 && live.labels > 0);
}

test("clearing the scene takes the connection builder's annotations, weld marks and every highlight with the models", async () => {
  const { viewport, live } = countingViewport();
  const release = setActiveViewport(viewport);
  try {
    buildPreview(live);
    highlightObjects("some-tool", [{ model: "m1", id: "1" }], { color: "#00ff00" });
    assert.equal(live.marks, 2, "the weld mark and the tool's highlight");
    await clearScene({ reason: "test" });
    assert.equal(live.cleared, 1, "the models of the viewport on screen");
    assert.equal(useModelState.getState().loadedSourceNames.size, 0);
    assert.equal(live.lines, 0, "the member axes");
    assert.equal(live.labels, 0, "the labels");
    assert.equal(live.marks, 0, "the weld marks and the highlights");
  } finally {
    release();
  }
});

test("unloading the preview alone takes its annotations and marks; unloading another model leaves them", async () => {
  const { viewport, live } = countingViewport();
  const release = setActiveViewport(viewport);
  try {
    buildPreview(live);
    useModelState.getState().unregisterLoadedSource(OTHER);
    assert.ok(live.lines > 0 && live.labels > 0, "another model went: the joint's annotations stay");
    assert.equal(live.marks, 1);
    useModelState.getState().unregisterLoadedSource(PREVIEW);
    assert.equal(live.lines + live.labels, 0, "the preview went: its annotations with it");
    assert.equal(live.marks, 0, "and its weld marks");
  } finally {
    release();
    await clearScene({ reason: "test" });
  }
});

test("a replace keeps what its own load sets, and the queue", async () => {
  const { viewport } = countingViewport();
  const release = setActiveViewport(viewport);
  try {
    const { useLoadQueueStore } = await import("@/state/loadQueueStore");
    useModelState.getState().setModelUrl("blob:next", null);
    // As queued, without starting it (enqueue starts a load at once).
    useLoadQueueStore.setState({ queued: [{ name: "queued.glb" }] } as never);
    await clearScene({ reason: "test", replacing: true, keepQueuedLoads: true });
    assert.equal(useModelState.getState().modelUrl, "blob:next", "the URL the replace is loading");
    assert.ok(useLoadQueueStore.getState().queued.some((t) => t.name === "queued.glb"));
    await clearScene({ reason: "test" });
    assert.equal(useModelState.getState().modelUrl, null);
    assert.ok(!useLoadQueueStore.getState().queued.some((t) => t.name === "queued.glb"), "a Clear drops the queue");
  } finally {
    release();
  }
});

// ---- the one path, held in the source ------------------------------------------------------------

const SRC = join(process.cwd(), "src");
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "__tests__" && name !== "node_modules") sources(p, out);
    } else if (/\.(ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}
const rel = (p: string) => relative(SRC, p).replace(/\\/g, "/");
const files = sources(SRC).map((p) => ({ path: rel(p), text: readFileSync(p, "utf8") }));

test("nothing but clearScene calls the scene's teardown primitives", () => {
  // Each primitive, and the files allowed to call it: clearScene, and a viewport's own `clearModels`.
  const primitives: [RegExp, string[]][] = [
    [/\bclearThreeSceneModels\(\)/, ["utils/scene/handlers/clear_scene.ts", "utils/scene/clearThreeSceneModels.ts", "viewport/api/threeViewport.ts"]],
    [/\.clearModels\(\)/, ["utils/scene/handlers/clear_scene.ts", "viewport/native/nativeViewportApi.ts"]],
    [/\.clearLoadedSources\(\)/, ["utils/scene/handlers/clear_scene.ts"]],
    // What the plugins drew over the scene, and telling them it was cleared (plugin API 1.30.0).
    [/\bremoveAllPluginDrawings\(\)/, ["utils/scene/handlers/clear_scene.ts", "plugins/sceneOverlays.ts"]],
    [/\breleaseDrawnLayers\(\)/, ["plugins/sceneOverlays.ts", "plugins/sceneLayers.ts"]],
    [/\bnotifySceneCleared\(/, ["utils/scene/handlers/clear_scene.ts", "plugins/sceneCleared.ts"]],
  ];
  for (const [pattern, allowed] of primitives) {
    const callers = files.filter((f) => pattern.test(f.text)).map((f) => f.path);
    assert.deepEqual(callers.filter((p) => !allowed.includes(p)), [], `${pattern} is called only from ${allowed.join(", ")}`);
  }
});

test("every trigger that empties the scene goes through clearScene", () => {
  // The storage browser's Clear, Remove all (Scene tab, tree menu, command), a plugin's clearScene, a
  // scope switch, the gallery, a re-convert, unloading a streamed result, the replace on either
  // viewport (a file opened, a server message, a renderer switch handing the models over), and the
  // older name plugins have for it.
  const triggers = [
    "components/storage/browser/useBulkActions.ts",
    "layout/sceneActions.ts",
    "plugins/context.ts",
    "utils/scope/scopeChange.ts",
    "components/viewer/GalleryControls.tsx",
    "components/info_box_scene/SourceSection.tsx",
    "utils/scene/handlers/unload_any_source.ts",
    "utils/scene/handlers/update_scene_from_message.ts",
    "components/viewer/sceneHelpers/loadModel.ts",
    "utils/scene/handlers/clear_loaded_model.ts",
  ];
  for (const path of triggers) {
    const text = files.find((f) => f.path === path)?.text;
    assert.ok(text, `${path} exists`);
    assert.match(text!, /clear_scene/, `${path} reaches the one teardown`);
    assert.match(text!, /clearScene\(/, `${path} calls clearScene`);
  }
  // The older name is that function, not a teardown of its own.
  const alias = files.find((f) => f.path === "utils/scene/handlers/clear_loaded_model.ts")!.text;
  assert.ok(!/clearTreeData|clearLoadedSources|clearModels/.test(alias), "clear_loaded_model tears nothing down itself");
});
