// A selected weld of the connection builder's preview is drawn in the selection's colour, on either
// viewport. The welds are drawn in a weld colour so they can be told from their members: on the
// three.js-free viewport as a mark (`connectionWeldMarks`), which the viewport draws OVER the
// selection, so a selected weld stayed the weld colour; on the three.js viewport in their meshes'
// vertex colours, under the selection's overlay -- but the weld colour was a blue next to the
// selection's blue, so a selected weld looked the same as before.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ObjectRef, ViewportApi } from "@/viewport/api/viewportApi";

await import("../fixtures/panelRealm");
// Saved before the default changed: every overlay is stored whenever one is toggled.
globalThis.localStorage.setItem(
  "ada-connection-builder:overlays",
  JSON.stringify({ axes: false, welds: true, weldSymbols: true, weldDetails: true, showWelds: true, tintWelds: true, weldColour: "#3b82f6" }),
);

const { markWelds, clearWeldMarks, unselectedWelds } = await import("@/utils/scene/connectionWeldMarks");
const { DEFAULT_OVERLAYS, useConnectionBuilderStore } = await import("@/state/connectionBuilderStore");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { useModelState } = await import("@/state/modelState");
const { useTreeViewStore } = await import("@/state/treeViewStore");

const PREVIEW = "connection-preview:box_to_box#3";

/** A viewport with a selection of its own, recording what it is asked to mark. */
function selectingViewport() {
  let selected: ObjectRef[] = [];
  const listeners = new Set<() => void>();
  const marks: { value: Map<string, Map<string, string>> } = { value: new Map() };
  const viewport = {
    kind: "native",
    selection: () => selected,
    onSelectionChange: (l: () => void) => {
      listeners.add(l);
      return () => void listeners.delete(l);
    },
    setObjectMarks: (m: Map<string, Map<string, string>>) => void (marks.value = m),
    hide: () => {},
    show: () => {},
  } as unknown as ViewportApi;
  const select = (refs: ObjectRef[]) => {
    selected = refs;
    listeners.forEach((l) => l());
  };
  return { viewport, select, marks, listeners };
}

const markedIds = (m: Map<string, Map<string, string>>) => [...(m.get("m3")?.keys() ?? [])].sort();

test("a marked weld that is selected is left out of the mark, and marked again once let go", () => {
  const { viewport, select, marks, listeners } = selectingViewport();
  const release = setActiveViewport(viewport);
  try {
    useModelState.getState().registerLoadedSourceName(PREVIEW);
    useTreeViewStore.getState().setTreeData({
      id: "__roots__",
      name: "",
      children: [
        {
          id: "r",
          name: "preview",
          source_name: PREVIEW,
          model_key: "m3",
          children: [
            { id: "a", name: "w1", rangeId: 1 },
            { id: "b", name: "w2", rangeId: 2 },
          ],
        },
      ],
    } as never);
    assert.equal(markWelds(PREVIEW, ["w1", "w2"], { colour: DEFAULT_OVERLAYS.weldColour, hidden: false }), 2);
    assert.deepEqual(markedIds(marks.value), ["1", "2"]);
    // Picked in 3D (or from the Connections tab): drawn in the selection's colour, not the weld's.
    select([{ model: "m3", id: "2" }]);
    assert.deepEqual(markedIds(marks.value), ["1"]);
    select([]);
    assert.deepEqual(markedIds(marks.value), ["1", "2"]);
    clearWeldMarks();
    assert.equal(listeners.size, 0, "stops following the selection");
    assert.deepEqual(markedIds(marks.value), []);
  } finally {
    release();
  }
});

test("unselectedWelds compares by model and id", () => {
  const refs = [
    { model: "a", id: "1" },
    { model: "b", id: "1" },
  ];
  assert.deepEqual(unselectedWelds(refs, [{ model: "b", id: "1" }]), [{ model: "a", id: "1" }]);
  assert.deepEqual(unselectedWelds(refs, []), refs);
});

test("on the three.js viewport a tinted weld that is selected is drawn by the selection's overlay, in a colour far from the weld's", async () => {
  const THREE = await import("three");
  const { CustomBatchedMesh } = await import("@/utils/mesh_select/CustomBatchedMesh");
  const { selectedMaterial } = await import("@/utils/default_materials");
  // Two triangles: a member (r0) and a weld (r1), as `connectionWeldTint` paints them.
  const pos = new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 2, 0, 0, 3, 0, 0, 3, 1, 0]);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setIndex(new THREE.BufferAttribute(Uint32Array.from({ length: 6 }, (_, i) => i), 1));
  const ranges = new Map<string, [number, number]>([
    ["r0", [0, 3]],
    ["r1", [3, 3]],
  ]);
  const m = new CustomBatchedMesh(g, new THREE.MeshStandardMaterial({ color: 0x888888 }), ranges, "m", true, null);
  const tint = new THREE.Color(DEFAULT_OVERLAYS.weldColour);
  m.setRangeColors(new Map([["r1", tint]]), new THREE.Color(0.5, 0.5, 0.5));
  m.updateSelectionGroups(["r1"]);
  const overlay = m.children.find((c) => m.isHighlightOverlay(c)) as InstanceType<typeof THREE.Mesh> | undefined;
  assert.ok(overlay?.visible, "the selection overlay is drawn");
  const groups = (overlay!.geometry as InstanceType<typeof THREE.BufferGeometry>).groups.filter((gr) => gr.materialIndex === 0);
  assert.deepEqual(
    groups.map((gr) => [gr.start, gr.count]),
    [[3, 3]],
    "over the weld",
  );
  const sel = selectedMaterial.color;
  const distance = Math.hypot(sel.r - tint.r, sel.g - tint.g, sel.b - tint.b);
  assert.ok(distance > 0.5, `the selection's colour stands out from the weld colour (${distance.toFixed(2)})`);
  const old = new THREE.Color("#3b82f6");
  assert.ok(Math.hypot(sel.r - old.r, sel.g - old.g, sel.b - old.b) < 0.3, "as the old weld colour did not");
});

test("the weld colour is not the selection's blue, and a stored old default reads as the new one", () => {
  assert.notEqual(DEFAULT_OVERLAYS.weldColour.toLowerCase(), "#3b82f6");
  const overlays = useConnectionBuilderStore.getState().overlays;
  assert.equal(overlays.weldColour, DEFAULT_OVERLAYS.weldColour);
  assert.equal(overlays.axes, false, "the rest of what was stored stays");
});
