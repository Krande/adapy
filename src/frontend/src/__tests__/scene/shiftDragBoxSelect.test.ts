// The selection rectangle on the three.js viewport: a drag the camera mapping calls "select" (Shift +
// left by default, any left drag in the GeniE controls), rightwards a window (wholly inside), leftwards a
// crossing (touching); held with Shift or Ctrl it adds to the selection, else it replaces it. It selects
// the scene's objects in every mode -- also while a procedural model is open behind the mode with its
// layer hidden, which used to turn the gesture off altogether -- and the builder's CELLS while the builder
// edits them. Driven through the real pointer handler with a stand-in container, window and viewport.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const THREE = await import("three");
const { setupPointerHandler } = await import("@/components/viewer/sceneHelpers/setupPointerHandler");
const { BUILDER_LAYER, builderLayerEditing, cellsInScreenRect, drawnCellBounds } = await import("@/components/viewer/cellbuilder/cellBoxSelect");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { useCameraControlsStore } = await import("@/state/cameraControlsStore");
const { CAMERA_PRESETS, cloneMapping } = await import("@/utils/camera/cameraMapping");
const { rectTakesBounds } = await import("@/utils/bboxSelection/screenRect");
const { KeySet } = await import("@/viewport/native/nativeBoxSelection");
const { objectKey } = await import("@/viewport/native/nativeScene");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;
type ObjectRef = import("@/viewport/api/viewportApi").ObjectRef;
type SetSelectionOptions = import("@/viewport/api/viewportApi").SetSelectionOptions;
type NdcRect = import("@/utils/bboxSelection/screenRect").NdcRect;
type ScreenBoxMode = import("@/utils/bboxSelection/screenRect").ScreenBoxMode;

// A 200 x 200 px view on an orthographic camera 20 m wide, looking down -z at the origin: a world
// point (x, y) is at client (100 + 10x, 100 - 10y).
const VIEW = { left: 0, top: 0, width: 200, height: 200, right: 200, bottom: 200, x: 0, y: 0 };
const camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 100);
camera.position.set(0, 0, 50);
camera.lookAt(0, 0, 0);
camera.updateMatrixWorld();
const viewProj = () => new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).elements;

// The same three things as scene objects and as cells: A inside the rectangle below, B reaching
// out of it, C far away.
const BOXES: Record<string, number[]> = { A: [1, 1, 0, 3, 3, 1], B: [4, 2, 0, 9, 4, 1], C: [-9, -9, 0, -7, -7, 1] };
/** Client points of the rectangle over world x 0.5..7, y 0.5..6. */
const RIGHTWARDS = [{ x: 105, y: 40 }, { x: 170, y: 95 }] as const;
const LEFTWARDS = [{ x: 170, y: 40 }, { x: 105, y: 95 }] as const;

function stage() {
  const scene = new THREE.Scene();
  const layer = new THREE.Group();
  layer.name = BUILDER_LAYER;
  const cellsGroup = new THREE.Group();
  layer.add(cellsGroup);
  scene.add(layer);
  for (const [id, b] of Object.entries(BOXES)) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(b[3] - b[0], b[4] - b[1], b[5] - b[2]), new THREE.MeshBasicMaterial());
    mesh.position.set((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2);
    mesh.userData.__cellId = id;
    cellsGroup.add(mesh);
  }
  return { scene, layer };
}

function viewport() {
  const ids = Object.keys(BOXES);
  const calls: { mode: ScreenBoxMode; ids: string[]; add: boolean }[] = [];
  let pending: ScreenBoxMode = "window";
  const api = {
    kind: "three",
    captureState: () => ({ selected: 0 }),
    stateChange: () => ({ selection: false, visibility: false, hidden: 0, shown: 0, after: () => ({ selected: 0 }) }),
    restoreState: () => {},
    objectsInScreenRect: (rect: NdcRect, mode: ScreenBoxMode) => {
      pending = mode;
      return new KeySet(ids.filter((id) => rectTakesBounds(BOXES[id], 0, viewProj(), rect, mode)).map((id) => objectKey("m", id)));
    },
    setSelection: (refs: Iterable<ObjectRef>, options: SetSelectionOptions = {}) => {
      const taken = Array.from(refs, (r) => r.id);
      calls.push({ mode: pending, ids: taken.sort(), add: !!options.add });
      options.within?.();
      return taken.length;
    },
  } as unknown as ViewportApi;
  return { api, calls };
}

/** The pointer handler on a stand-in container; `drag` presses with Shift at `from`, moves and
 *  releases at `to`, as a mouse would. */
function handler(scene: InstanceType<typeof THREE.Scene>) {
  type Fn = (e: unknown) => void;
  const own = new Map<string, Fn[]>();
  const onWindow = new Map<string, Set<Fn>>();
  const w = window as unknown as { addEventListener: unknown; removeEventListener: unknown };
  const saved = { add: w.addEventListener, remove: w.removeEventListener };
  w.addEventListener = (type: string, fn: Fn) => void (onWindow.get(type) ?? onWindow.set(type, new Set()).get(type)!).add(fn);
  w.removeEventListener = (type: string, fn: Fn) => void onWindow.get(type)?.delete(fn);
  const doc = document as unknown as { createElement: unknown };
  const savedCreate = doc.createElement;
  doc.createElement = () => ({ style: {}, remove() {} });
  const container = {
    addEventListener: (type: string, fn: Fn) => void (own.get(type) ?? own.set(type, []).get(type)!).push(fn),
    getBoundingClientRect: () => VIEW,
    appendChild() {},
  };
  const renderer = { domElement: { getBoundingClientRect: () => VIEW } };
  const detach = setupPointerHandler(container as never, camera as never, scene, renderer as never);
  const fire = (fns: Iterable<Fn> | undefined, e: object) => {
    for (const fn of [...(fns ?? [])]) fn(e);
  };
  type Mods = { shiftKey?: boolean; ctrlKey?: boolean; button?: number };
  const event = (p: { x: number; y: number }, mods: Mods) => ({
    button: 0, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, pointerType: "mouse", ...mods, clientX: p.x, clientY: p.y,
    stopPropagation() {}, preventDefault() {},
  });
  return {
    /** A drag with Shift held, unless `mods` says otherwise. */
    drag(from: { x: number; y: number }, to: { x: number; y: number }, mods: Mods = { shiftKey: true }) {
      fire(own.get("pointerdown"), event(from, mods));
      fire(onWindow.get("pointermove"), event({ x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 }, mods));
      fire(onWindow.get("pointermove"), event(to, mods));
      fire(onWindow.get("pointerup"), event(to, mods));
    },
    done() {
      detach();
      w.addEventListener = saved.add;
      w.removeEventListener = saved.remove;
      doc.createElement = savedCreate;
    },
  };
}

const builderBefore = useCellBuilderStore.getState();
function openModel(patch: Record<string, unknown> = {}) {
  useCellBuilderStore.setState({
    active: { modelId: "m1", name: "boxsel", revision: 1 },
    cells: { A: { id: "A" }, B: { id: "B" }, C: { id: "C" } },
    cellsVisible: true,
    repMode: "topology",
    selection: null,
    selectedCellIds: [],
    ...patch,
  } as never);
}
function closeModel() {
  useCellBuilderStore.setState({
    active: builderBefore.active, cells: builderBefore.cells, cellsVisible: builderBefore.cellsVisible,
    repMode: builderBefore.repMode, selection: builderBefore.selection, selectedCellIds: builderBefore.selectedCellIds,
  });
}

test("the cells a rectangle takes: a window those wholly inside, a crossing those it touches", () => {
  const { layer } = stage();
  const bounds = drawnCellBounds(layer);
  assert.deepEqual([...bounds.keys()].sort(), ["A", "B", "C"]);
  assert.deepEqual(bounds.get("B"), BOXES.B);
  const rect = { x0: 0.05, y0: 0.05, x1: 0.7, y1: 0.6 };
  assert.deepEqual(cellsInScreenRect(bounds, viewProj(), rect, "window"), ["A"]);
  assert.deepEqual(cellsInScreenRect(bounds, viewProj(), rect, "crossing").sort(), ["A", "B"]);
  // A cell hidden on its own (its eye, or "Included only") is not drawn, so not taken.
  layer.children[0].children[0].visible = false;
  assert.deepEqual([...drawnCellBounds(layer).keys()].sort(), ["B", "C"]);
});

test("the builder takes the gesture only while it edits its cells", () => {
  const { scene, layer } = stage();
  try {
    assert.equal(builderLayerEditing(scene), null, "no procedural model open");
    openModel();
    assert.equal(builderLayerEditing(scene), layer, "Topology Input: the cells");
    layer.visible = false;
    assert.equal(builderLayerEditing(scene), null, "a mode that hides the builder's layer (Output, FEM)");
    layer.visible = true;
    openModel({ cellsVisible: false });
    assert.equal(builderLayerEditing(scene), null, "cells hidden");
    openModel({ repMode: "simulation" });
    assert.equal(builderLayerEditing(scene), null, "cells drawn to compare with a compiled model");
  } finally {
    closeModel();
  }
});

test("Shift+drag selects the scene's objects: rightwards a window, leftwards a crossing, added", () => {
  const { scene, layer } = stage();
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  const h = handler(scene);
  try {
    // No procedural model open.
    h.drag(...RIGHTWARDS);
    h.drag(...LEFTWARDS);
    // A procedural model open behind a mode that hides its layer (Output, the FEM modes): the
    // rectangle is still the scene's. This is what turned the gesture off before.
    openModel();
    layer.visible = false;
    h.drag(...RIGHTWARDS);
    h.drag(...LEFTWARDS);
    assert.deepEqual(v.calls, [
      { mode: "window", ids: ["A"], add: true },
      { mode: "crossing", ids: ["A", "B"], add: true },
      { mode: "window", ids: ["A"], add: true },
      { mode: "crossing", ids: ["A", "B"], add: true },
    ]);
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, [], "no cell was selected");
  } finally {
    h.done();
    withdraw();
    closeModel();
  }
});

test("Shift+drag in the builder (Topology Input) selects cells: a window, then a crossing adds to it", () => {
  const { scene } = stage();
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  const h = handler(scene);
  try {
    openModel();
    h.drag(...RIGHTWARDS);
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, ["A"]);
    assert.deepEqual(useCellBuilderStore.getState().selection, { kind: "cell", cellId: "A" });
    h.drag(...LEFTWARDS);
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, ["A", "B"]);
    assert.deepEqual(useCellBuilderStore.getState().selection, { kind: "cell", cellId: "A" }, "the first stays the primary");
    assert.equal(v.calls.length, 0, "the scene's selection is left alone");
    // A press that never moves is a click (the builder's own), not a rectangle.
    h.drag(RIGHTWARDS[0], RIGHTWARDS[0]);
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, ["A", "B"]);
  } finally {
    h.done();
    withdraw();
    closeModel();
  }
});

test("the GeniE controls: a plain left drag draws the rectangle and replaces the selection; Shift or Ctrl adds; the right button never selects", () => {
  const { scene } = stage();
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  const before = useCameraControlsStore.getState().mapping;
  useCameraControlsStore.getState().setMapping(cloneMapping(CAMERA_PRESETS.genie.mapping));
  const h = handler(scene);
  try {
    h.drag(...RIGHTWARDS, {});
    h.drag(...LEFTWARDS, { shiftKey: true });
    h.drag(...RIGHTWARDS, { ctrlKey: true });
    // The right button orbits, Shift + right zooms, Ctrl + right pans: none of them selects.
    h.drag(...RIGHTWARDS, { button: 2 });
    h.drag(...RIGHTWARDS, { button: 2, shiftKey: true });
    assert.deepEqual(v.calls, [
      { mode: "window", ids: ["A"], add: false },
      { mode: "crossing", ids: ["A", "B"], add: true },
      { mode: "window", ids: ["A"], add: true },
    ]);
    // In the builder, a plain drag replaces the cell selection.
    openModel({ selectedCellIds: ["C"], selection: { kind: "cell", cellId: "C" } });
    h.drag(...LEFTWARDS, {});
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, ["A", "B"]);
  } finally {
    h.done();
    withdraw();
    closeModel();
    useCameraControlsStore.getState().setMapping(before);
  }
});

test("the default controls: a plain or Ctrl + left drag is the camera's, not a rectangle", () => {
  const { scene } = stage();
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  const h = handler(scene);
  try {
    h.drag(...RIGHTWARDS, {});
    h.drag(...RIGHTWARDS, { ctrlKey: true });
    assert.equal(v.calls.length, 0);
  } finally {
    h.done();
    withdraw();
  }
});
