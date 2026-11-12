// Number labels (what is labelled, how many, where) and colour by property
// (sources, the model colouring, the legend, reset), plus the commands and
// toolbars over them.

import assert from "node:assert/strict";
import { test } from "node:test";
import type * as THREE from "three";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const T3 = await import("three");
const C = await import("@/layout/commands");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { CORE_COMMAND_ICONS } = await import("@/layout/commandIcons");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const T = await import("@/layout/ModeToolbars");
const D = await import("@/layout/desktop/desktopShell");
const M = await import("@/layout/shellModes");
const { ColorByLegend } = await import("@/layout/ColorByLegend");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const { useColorByStore } = await import("@/state/colorByStore");
const { useLabelsStore } = await import("@/state/labelsStore");
const { loadedSourceGroups } = await import("@/state/modelState");
const { CustomBatchedMesh } = await import("@/utils/mesh_select/CustomBatchedMesh");
const L = await import("@/utils/scene/fea/numberLabels");
const CB = await import("@/utils/scene/colorBy");
const V = await import("@/viewer-core");
const { readFileSync } = await import("node:fs");

const html = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));

/** Two triangles sharing an edge: elements E1 (nodes 0,1,2) and E2 (nodes 1,2,3). */
function mesh(): THREE.Mesh {
  const g = new T3.BufferGeometry();
  g.setAttribute("position", new T3.BufferAttribute(new Float32Array([-2, -1, 0, 2, -1, 0, 0, 1, 0, 3, 3, 0]), 3));
  g.setIndex([0, 1, 2, 1, 2, 3]);
  const m = new T3.Mesh(g) as THREE.Mesh & { drawRanges: Map<string, [number, number]>; hidden: Set<string>; isRangeHidden(id: string): boolean };
  m.drawRanges = new Map([["E1", [0, 3]], ["E2", [3, 3]]]);
  m.hidden = new Set();
  m.isRangeHidden = (id: string) => m.hidden.has(id);
  m.updateMatrixWorld();
  return m;
}

function camera(): THREE.PerspectiveCamera {
  const c = new T3.PerspectiveCamera(50, 1, 0.1, 100);
  c.position.set(0, 0, 12);
  c.lookAt(0, 0, 0);
  c.updateMatrixWorld();
  c.updateProjectionMatrix();
  return c;
}

const OPTS = { nodes: true, elements: false, cap: 100, scanCap: 100, width: 800, height: 600 };

test("node labels: each node once, numbered from 1 unless a table says otherwise", () => {
  const out = L.collectLabels(mesh(), camera(), OPTS);
  assert.deepEqual(out.map((l) => l.text).sort(), ["1", "2", "3", "4"]);
  assert.ok(out.every((l) => l.x >= 0 && l.x <= 800 && l.y >= 0 && l.y <= 600));
  const solver = [101, 102, 103, 104];
  const named = L.collectLabels(mesh(), camera(), { ...OPTS, nodeText: (i) => String(solver[i]) });
  assert.deepEqual(named.map((l) => l.text).sort(), ["101", "102", "103", "104"]);
});

test("element labels sit at the element's centre and show the number, not the key's letter", () => {
  const out = L.collectLabels(mesh(), camera(), { ...OPTS, nodes: false, elements: true });
  assert.deepEqual(out.map((l) => l.text).sort(), ["1", "2"]);
  assert.equal(L.elementText("E1234"), "1234");
  assert.equal(L.elementText("abc"), "abc");
});

test("a selection narrows the labels to its elements, and hidden elements get none", () => {
  const m = mesh() as ReturnType<typeof mesh> & { hidden: Set<string> };
  const only = L.collectLabels(m, camera(), { ...OPTS, nodes: false, elements: true, selected: new Set(["E2"]) });
  assert.deepEqual(only.map((l) => l.text), ["2"]);
  m.hidden.add("E1");
  const rest = L.collectLabels(m, camera(), { ...OPTS, nodes: false, elements: true });
  assert.deepEqual(rest.map((l) => l.text), ["2"]);
});

test("the count is capped and thinned on a screen grid, and what is off screen or behind is dropped", () => {
  assert.equal(L.collectLabels(mesh(), camera(), { ...OPTS, cap: 2 }).length, 2);
  // One huge cell: a single label wherever the points are.
  assert.equal(L.collectLabels(mesh(), camera(), { ...OPTS, cellW: 5000, cellH: 5000 }).length, 1);
  // Looking away from the model: nothing.
  const away = camera();
  away.lookAt(0, 0, 100);
  away.updateMatrixWorld();
  assert.deepEqual(L.collectLabels(mesh(), away, OPTS), []);
  // Nothing asked for, no view, no mesh.
  assert.deepEqual(L.collectLabels(mesh(), camera(), { ...OPTS, nodes: false }), []);
  assert.deepEqual(L.collectLabels(mesh(), camera(), { ...OPTS, width: 0 }), []);
  assert.deepEqual(L.collectLabels(null, camera(), OPTS), []);
});

test("a big mesh is sampled, not scanned: the pass costs at most scanCap elements", () => {
  const g = new T3.BufferGeometry();
  const n = 3000;
  const pos = new Float32Array(n * 9);
  const idx: number[] = [];
  const ranges = new Map<string, [number, number]>();
  for (let e = 0; e < n; e++) {
    for (let k = 0; k < 3; k++) {
      pos.set([(e % 60) * 0.1 - 3, Math.floor(e / 60) * 0.05 - 1.2, k * 0.01], e * 9 + k * 3);
      idx.push(e * 3 + k);
    }
    ranges.set(`E${e + 1}`, [e * 3, 3]);
  }
  g.setAttribute("position", new T3.BufferAttribute(pos, 3));
  g.setIndex(idx);
  const m = new T3.Mesh(g) as THREE.Mesh & { drawRanges: typeof ranges };
  m.drawRanges = ranges;
  m.updateMatrixWorld();
  const out = L.collectLabels(m, camera(), { ...OPTS, nodes: false, elements: true, scanCap: 100, cap: 1000 });
  assert.ok(out.length > 0 && out.length <= 100, `${out.length} labels from a scan of 100`);
});

test("label commands need a result, toggle the store, and have icons", () => {
  C.resetCommands();
  registerCoreCommands();
  useFeaAnimationStore.getState().reset();
  for (const id of [ID.labelsNodes, ID.labelsElements, ID.colorByNext, ID.colorByModel, ID.colorByReset]) {
    assert.ok(C.getCommand(id), `${id} is registered`);
    assert.ok(CORE_COMMAND_ICONS[id], `${id} has an icon`);
  }
  assert.equal(C.getCommand(ID.labelsNodes)!.disabledReason?.(), "No result is loaded");
  useFeaAnimationStore.getState().setSessionActive(true);
  assert.equal(C.getCommand(ID.labelsNodes)!.disabledReason?.(), null);
  assert.ok(C.runCommand(ID.labelsNodes));
  assert.equal(useLabelsStore.getState().nodes, true);
  assert.equal(C.getCommand(ID.labelsNodes)!.checked?.(), true);
  assert.ok(C.runCommand(ID.labelsElements));
  assert.equal(useLabelsStore.getState().elements, true);
  assert.ok(C.runCommand(ID.labelsNodes));
  assert.equal(useLabelsStore.getState().nodes, false);
  useLabelsStore.getState().setElements(false);
  useFeaAnimationStore.getState().reset();
  C.resetCommands();
});

/** A stand-in batched mesh that records the colours it is given. */
function stubMesh(ids: string[]) {
  const m = Object.create(CustomBatchedMesh.prototype) as InstanceType<typeof CustomBatchedMesh> & { painted?: Map<string, THREE.Color>; resets: number };
  Object.assign(m, { drawRanges: new Map(ids.map((i) => [i, [0, 3]])), resets: 0 });
  m.setRangeColors = ((c: Map<string, THREE.Color>) => void (m.painted = c)) as never;
  m.disableVertexColorsAndResetMaterial = (() => void m.resets++) as never;
  return m;
}

test("colour by model: each model its own colour, a legend, and a reset that reaches exactly those meshes", async () => {
  const a = stubMesh(["1", "2"]);
  const b = stubMesh(["7"]);
  const group = (m: unknown) => ({ traverse: (cb: (o: unknown) => void) => cb(m) }) as unknown as THREE.Group;
  loadedSourceGroups.clear();
  loadedSourceGroups.set("beta.glb", group(b));
  loadedSourceGroups.set("alpha.glb", group(a));
  assert.deepEqual(CB.colorBySources().map((s) => s.id), ["model"]);

  await CB.applyColorBy("model");
  const st = useColorByStore.getState();
  assert.equal(st.activeId, "model");
  assert.deepEqual(st.legend!.entries.map((e) => e.label), ["alpha.glb", "beta.glb"]);
  assert.notEqual(st.legend!.entries[0].color, st.legend!.entries[1].color);
  assert.deepEqual([...a.painted!.keys()], ["1", "2"]);
  assert.notDeepEqual(a.painted!.get("1"), b.painted!.get("7"));
  assert.match(html(React.createElement(ColorByLegend)), /^$/, "server render reads the initial store: nothing drawn");

  await CB.resetColorBy();
  assert.equal(useColorByStore.getState().activeId, null);
  assert.equal(useColorByStore.getState().legend, null);
  assert.equal(a.resets, 1);
  assert.equal(b.resets, 1);
  loadedSourceGroups.clear();
});

test("a registered source joins the picker and the cycle, and reset calls its own", async () => {
  CB.resetColorBySources();
  loadedSourceGroups.clear();
  const calls: string[] = [];
  CB.registerColorBySource(
    {
      id: "plug:kind",
      label: "Kind",
      apply: () => {
        calls.push("apply");
        return { title: "Kind", entries: [{ label: "Pipe", color: "rgb(1, 2, 3)" }] };
      },
      reset: () => void calls.push("reset"),
    },
    "test",
  );
  CB.registerColorBySource({ id: "plug:off", label: "Off", available: () => false, apply: () => {} }, "test");
  assert.deepEqual(CB.colorBySources().map((s) => s.id), ["plug:kind"]);
  assert.equal(CB.nextColorBySource(), "plug:kind");
  await CB.applyColorBy("plug:kind");
  assert.equal(useColorByStore.getState().legend!.entries[0].label, "Pipe");
  assert.equal(CB.nextColorBySource(), "plug:kind", "wraps");
  await CB.resetColorBy();
  assert.deepEqual(calls, ["apply", "reset"]);
  await assert.rejects(CB.applyColorBy("nope"), /Unknown colour source/);
  CB.resetColorBySources();
});

test("an FEA result's property fields are offered, and the others are not", () => {
  const fea = useFeaAnimationStore.getState();
  fea.reset();
  assert.deepEqual(CB.feaPropertyFields(), []);
  fea.setSessionActive(true);
  fea.setManifest({
    fields: [
      { name_canonical: "stress", category: "result" },
      { name_canonical: "section", category: "property", group_path: ["Properties", "Section"] },
      { name_canonical: "thickness", category: "property" },
    ],
  } as never);
  assert.deepEqual(CB.feaPropertyFields(), [
    { id: "fea:section", label: "Properties / Section" },
    { id: "fea:thickness", label: "thickness" },
  ]);
  assert.deepEqual(CB.colorBySources().map((s) => s.id), ["fea:section", "fea:thickness"]);
  fea.reset();
});

test("colour-by commands: reset needs something coloured, the model one needs two models", () => {
  C.resetCommands();
  registerCoreCommands();
  loadedSourceGroups.clear();
  useColorByStore.getState().set({ activeId: null, legend: null });
  assert.equal(C.getCommand(ID.colorByReset)!.disabledReason?.(), "Nothing is recoloured or coloured by property");
  assert.match(C.getCommand(ID.colorByModel)!.disabledReason?.() ?? "", /two or more/);
  useColorByStore.getState().set({ activeId: "model" });
  assert.equal(C.getCommand(ID.colorByReset)!.disabledReason?.(), null);
  assert.equal(C.getCommand(ID.colorByModel)!.checked?.(), true);
  useColorByStore.getState().set({ activeId: null });
  C.resetCommands();
});

test("the toolbars: representation carries the label switches, colorBy a picker and a reset", () => {
  C.resetCommands();
  registerCoreCommands();
  const rep = html(React.createElement(T.RepresentationToolbar));
  assert.match(rep, /data-command="core:labels-nodes"/);
  assert.match(rep, /data-command="core:labels-elements"/);
  const by = html(React.createElement(T.ColorByToolbar));
  assert.match(by, /data-mode-toolbar="colorBy"/);
  assert.match(by, /Nothing to colour by/);
  assert.match(by, /data-command="core:color-by-reset"/);
  assert.ok((M.CORE_MODE_TOOLBAR_IDS as readonly string[]).includes("colorBy"));
  C.resetCommands();
});

test("the Desktop UI uses them: toolbars on both modes, labels and the colour-by legend placed", () => {
  const spec = D.desktopUiShellSpec();
  for (const m of spec.layout!.modes!.filter((x) => x.id !== "build")) assert.ok(m.toolbars!.includes("colorBy"), m.id);
  assert.equal(spec.chrome!.overlays!.labels, true);
  assert.ok(spec.chrome!.overlays!.colorByLegend);
});

test("the facade publishes them, and the API version moved", () => {
  assert.equal(V.VIEWER_CORE_API_VERSION, "1.20.0");
  // The scene entry cannot load outside a browser, so its exports are read from the source.
  const scene = readFileSync(new URL("../../viewer-core/scene.ts", import.meta.url), "utf8");
  for (const name of ["registerColorBySource", "applyColorBy", "resetColorBy", "useColorByStore", "useLabelsStore"]) {
    assert.match(scene, new RegExp(`\\b${name}\\b`), `${name} is exported`);
  }
  const facade = readFileSync(new URL("../../viewer-core/layout.ts", import.meta.url), "utf8");
  for (const [name, from] of [["NumberLabels", "NumberLabels"], ["ColorByLegend", "ColorByLegend"], ["ColorByToolbar", "ModeToolbars"]]) {
    assert.match(facade, new RegExp(`\\b${name}\\b[^;]*from "@/layout/${from}"`), `${name} is exported`);
  }
  const reg = readFileSync(new URL("../../plugins/registry.ts", import.meta.url), "utf8");
  assert.match(reg, /PLUGIN_API_VERSION = "1\.20\.0"/);
});
