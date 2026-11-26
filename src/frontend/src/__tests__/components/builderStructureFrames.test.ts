// The builder draws and edits each structure's items in a FRAME moved to the structure's origin
// (components/viewer/cellbuilder/structureFrames.ts): the items keep their stored, relative
// coordinates; the frames carry the origin. Moving a structure (setGroupOrigin) moves its frames,
// so everything in it is drawn where it now is with no rebuild; the edit frame follows the pick, the
// placement frame the structure an add mode places into, and the world <-> frame mapping
// (offsetVec / worldToModel) and the camera fit read the origins too.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { useModelState } = await import("@/state/modelState");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { offsetVec, worldToModel } = await import("@/components/viewer/cellbuilder/sceneContext");
const { editStructure, placeStructure, structureFrame, syncStructureFrames } = await import("@/components/viewer/cellbuilder/structureFrames");
const { rebuildRefs } = await import("@/components/viewer/cellbuilder/refsOverlay");
const { startFaceDrag } = await import("@/components/viewer/cellbuilder/pointerTools");
const { boxTarget } = await import("@/components/viewer/cellbuilder/gizmoBox");
const { cellsWorldBox } = await import("@/utils/scene/frameCells");
type Ctx = Parameters<typeof syncStructureFrames>[0];
type Vec3 = [number, number, number];

const st = () => useCellBuilderStore.getState();

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "A1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2, STRUCTURE_NAME: "A" },
    { NAME: "B1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2, STRUCTURE_NAME: "B" },
  ],
  // Each space's own equipment, centred well inside its box -- the riders the "Shift+D on a
  // space with equipment" tests below check are not carried off with a duplicate placed on top
  // of the space that still holds them.
  equipments: [
    { NAME: "A1-EQ", SPACE_NAME: "A1", GLOBAL_COORDS: true, X: 0.5, Y: 0.5, Z: 0, LX: 0.5, LY: 0.5, LZ: 0.5 },
    { NAME: "B1-EQ", SPACE_NAME: "B1", GLOBAL_COORDS: true, X: 0.5, Y: 0.5, Z: 0, LX: 0.5, LY: 0.5, LZ: 0.5 },
  ],
  // A global opening straddling A1's +X face (centre at x=2, within A1's [0,2] y/z extent) --
  // the "a global opening re-homes against its space's OWN-TICK position" test below drags it
  // together with A1 and checks it re-homes against A1's OWN-TICK position.
  openings: [{ NAME: "A1-DOOR", SUBTYPE: "door", X: 1.9, Y: 0.8, Z: 0.8, DX: 0.2, DY: 0.4, DZ: 0.4 }],
  groups: [
    { name: "A", blueprint: "bp" },
    { name: "B", blueprint: "bp", origin: [10, 0, 0] },
  ],
  boxes: [{ origin: [0, 0, 0], size: [1, 1, 1] }],
} as unknown as ProceduralDoc;

/** Enough of the builder scene for the frames, the refs and a face drag: the container at the
 *  viewer's model translation (1, 2, 3), its subgroups and frames. */
function makeCtx() {
  const container = new THREE.Group();
  container.position.set(1, 2, 3);
  const group = () => {
    const g = new THREE.Group();
    container.add(g);
    return g;
  };
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
  camera.position.set(11.5, -20, 3.5);
  camera.lookAt(11.5, 2.5, 3.5);
  camera.updateMatrixWorld(true);
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(new THREE.Vector2(0, 0), camera);
  const el = { style: {}, setPointerCapture() {}, releasePointerCapture() {} };
  return {
    host: { camera: () => camera, input: () => el, rect: () => ({ left: 0, top: 0, width: 100, height: 100 }), setCameraEnabled() {} },
    container,
    cellsGroup: group(),
    refsGroup: group(),
    portsGroup: group(),
    cadPreviewGroup: group(),
    cogGroup: group(),
    editFrame: group(),
    placeFrame: group(),
    portFrame: group(),
    refMeshById: new Map(),
    raycaster,
    drag: null,
  } as unknown as Ctx & { drag: { lineOrigin: THREE.Vector3 } | null };
}

const worldOf = (o: THREE.Object3D): Vec3 => {
  o.updateWorldMatrix(true, false);
  return new THREE.Vector3().setFromMatrixPosition(o.matrixWorld).toArray() as Vec3;
};
const cellId = (name: string) => Object.values(st().cells).find((c) => c.name === name)!.id;

test("offsetVec / worldToModel map the world to a frame: the model translation plus its structure's origin", () => {
  const ctx = makeCtx();
  ctx.editFrame.position.set(10, 0, 0);
  assert.deepEqual(offsetVec(ctx).toArray(), [11, 2, 3], "by default the edit frame's");
  assert.deepEqual(worldToModel(ctx, new THREE.Vector3(12, 3, 4)), [1, 1, 1]);
  // An explicit frame: another structure's origin, or none.
  assert.deepEqual(offsetVec(ctx, [0, 5, 0]).toArray(), [1, 7, 3]);
  assert.deepEqual(worldToModel(ctx, new THREE.Vector3(12, 3, 4), [0, 0, 0]), [11, 1, 1]);
  assert.deepEqual(worldToModel(ctx, new THREE.Vector3(12, 3, 4), ctx.placeFrame.position), [11, 1, 1]);
});

test("each structure's items draw in its frame; moving the structure moves the frames, and the edit and placement frames follow", () => {
  st().open("m", "Model", 1, doc);
  try {
    const ctx = makeCtx();
    const a = structureFrame(ctx.cellsGroup, "A", st().groups);
    const b = structureFrame(ctx.cellsGroup, "B", st().groups);
    assert.equal(structureFrame(ctx.cellsGroup, "B", st().groups), b, "one frame per structure");
    const box = new THREE.Mesh();
    box.position.set(1, 1, 1); // the stored (relative) centre of B1
    b.add(box);
    assert.deepEqual(worldOf(a), [1, 2, 3]);
    assert.deepEqual(worldOf(box), [12, 3, 4], "drawn at its structure's origin");

    st().setGroupOrigin("B", [20, 0, 5]);
    syncStructureFrames(ctx);
    assert.deepEqual(worldOf(box), [22, 3, 9], "moved with its structure, no rebuild");
    assert.deepEqual(box.position.toArray(), [1, 1, 1], "its own coordinates are as stored");

    // The edit frame: the pick's structure; the placement frame: the active one in an add mode.
    st().setSelection({ kind: "cell", cellId: cellId("B1") });
    syncStructureFrames(ctx);
    assert.equal(editStructure(st()), "B");
    assert.deepEqual(ctx.editFrame.position.toArray(), [20, 0, 5]);
    assert.deepEqual(ctx.placeFrame.position.toArray(), [20, 0, 5], "idle: the placement frame is the edit frame");
    st().setActiveGroup("A");
    st().setMode("add-cell");
    syncStructureFrames(ctx);
    assert.equal(placeStructure(st()), "A");
    assert.deepEqual(ctx.placeFrame.position.toArray(), [0, 0, 0], "adding: the structure the new box goes into");
    assert.deepEqual(ctx.editFrame.position.toArray(), [20, 0, 5], "the gizmo stays with the pick");
    st().setMode("idle");
  } finally {
    st().close();
  }
});

test("a plugin's ref draws in its structure's frame, and a face drag on it runs through where it is drawn", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "frames-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b) => ({
        key: "r",
        name: "r",
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
        structure: "B",
        edit: { move: true, resize: true },
      })),
    write: () => true,
  });
  try {
    const ctx = makeCtx();
    rebuildRefs(ctx);
    const mesh = ctx.refMeshById.get("frames-test:r")!;
    assert.deepEqual(mesh.position.toArray(), [0.5, 0.5, 0.5], "its own box");
    assert.deepEqual(worldOf(mesh), [11.5, 2.5, 3.5], "at B's origin");
    // The +X face drag's line runs through the box's centre in the world.
    const down = { clientX: 50, clientY: 50, pointerId: 1 } as unknown as PointerEvent;
    assert.equal(startFaceDrag(ctx, boxTarget(st(), "frames-test:r", true)!, 0, down, false), true);
    assert.deepEqual(ctx.drag!.lineOrigin.toArray(), [11.5, 2.5, 3.5]);
  } finally {
    off();
    st().close();
  }
});

test("fit selection / fit all frame the cells where they are drawn", () => {
  st().open("m", "Model", 1, doc);
  try {
    useModelState.getState().setTranslation(new THREE.Vector3(0, 0, 1));
    const one = cellsWorldBox([cellId("B1")])!;
    assert.deepEqual([one.min.toArray(), one.max.toArray()], [[10, 0, 1], [12, 2, 3]], "B1 at B's origin (and the model translation)");
    const all = cellsWorldBox("all")!;
    assert.deepEqual([all.min.toArray(), all.max.toArray()], [[0, 0, 1], [12, 2, 3]], "A1 at the model's origin, B1 at B's");
    assert.equal(cellsWorldBox([]), null);
  } finally {
    st().close();
  }
});

// --- G on a structure picked on its own -----------------------------------------------------------

const { onKeyDown, ROTATE_SCALE_HINT, clearsRotateScaleHint } = await import("@/components/viewer/cellbuilder/keyboard");
const { installCellGizmo } = await import("@/components/viewer/cellbuilder/cellGizmo");
const { syncGizmo } = await import("@/components/viewer/cellbuilder/resizeHandles");
const { movedStructureOrigin, pickedStructure, structureGizmoCentre } = await import("@/components/viewer/cellbuilder/structureFrames");
const { setFreeMove } = await import("@/utils/gizmo/freeMove");

/** The scene as the move gizmo needs it: makeCtx's, with a stand-in TransformControls whose
 *  events a test fires. */
function gizmoCtx() {
  const listeners = new Map<string, ((e: unknown) => void)[]>();
  const gizmo = {
    axis: null as string | null,
    object: null as unknown,
    dragging: false,
    camera: null as unknown,
    mode: "translate",
    showX: true,
    showY: true,
    showZ: true,
    enabled: true,
    snap: undefined as number | null | undefined,
    addEventListener: (t: string, l: (e: unknown) => void) => listeners.set(t, [...(listeners.get(t) ?? []), l]),
    attach(o: unknown) {
      this.object = o;
    },
    detach() {
      this.object = null;
    },
    setMode(m: string) {
      this.mode = m;
    },
    setTranslationSnap(s: number | null) {
      this.snap = s;
    },
    setRotationSnap() {},
  };
  const ctx = Object.assign(makeCtx(), {
    gizmo,
    gizmoHelper: { visible: false },
    gizmoProxy: new THREE.Object3D(),
    resizeGroup: new THREE.Group(),
    dimensionGroup: new THREE.Group(),
    moveMeasureGroup: new THREE.Group(),
    guideLine: { visible: false },
    snapMarker: { visible: false, position: new THREE.Vector3() },
    modalMove: null,
    moveMeasure: null,
    loftDragLast: null,
    translateEquip: [],
    structureDrag: null,
    numEntry: null,
    placeEntry: null,
    equipEntry: null,
    openEntry: null,
    loftActive: null,
    fire: (type: string, e: object = {}) => (listeners.get(type) ?? []).forEach((l) => l(e)),
  });
  (ctx.editFrame as THREE.Group).add(ctx.gizmoProxy);
  return ctx as unknown as Ctx & typeof ctx;
}

const key = (k: string) =>
  ({ key: k, code: `Key${k.toUpperCase()}`, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, target: null, preventDefault() {}, stopPropagation() {} }) as unknown as KeyboardEvent;

/** B picked on its own: open, nothing selected, B active. */
function pickB() {
  st().open("m", "Model", 1, doc);
  st().setSelection(null);
  st().setActiveGroup("B");
}

test("G on a structure picked on its own brings up the move gizmo on it; G again, or another pick, puts it away", () => {
  pickB();
  try {
    const ctx = gizmoCtx();
    assert.equal(pickedStructure(st()), "B");
    onKeyDown(ctx, key("g"));
    assert.equal(st().gizmoMode, "translate");
    // On the structure: at the centre of its items, in its frame (B1 is 2 x 2 x 2 at its origin).
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    assert.equal(ctx.gizmo.object, ctx.gizmoProxy, "attached");
    assert.deepEqual(ctx.gizmoProxy.position.toArray(), [1, 1, 1]);
    assert.deepEqual(worldOf(ctx.gizmoProxy), [12, 3, 4], "where B1's centre is drawn");
    onKeyDown(ctx, key("g"));
    assert.equal(st().gizmoMode, "none", "G again");
    onKeyDown(ctx, key("g"));
    st().setActiveGroup("A");
    assert.equal(st().gizmoMode, "none", "another structure picked");
    st().setActiveGroup("B");
    onKeyDown(ctx, key("g"));
    st().setSelection({ kind: "cell", cellId: cellId("B1") });
    assert.equal(st().gizmoMode, "none", "an item selected");
    assert.equal(pickedStructure(st()), undefined);
    // A structure with nothing in it: the gizmo sits on its origin.
    assert.deepEqual(structureGizmoCentre({ ...st(), cells: {} }, "B"), [0, 0, 0]);
  } finally {
    st().close();
  }
});

test("dragging a picked structure moves its frames live and writes ONE setGroupOrigin on release (one undo step)", () => {
  pickB();
  try {
    st().setGridStep(0.5);
    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    const frame = structureFrame(ctx.cellsGroup, "B", st().groups);
    st().setGizmoMode("translate");
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    let writes = 0;
    const real = st().setGroupOrigin;
    useCellBuilderStore.setState({ setGroupOrigin: (n: string, o: readonly [number, number, number]) => (writes++, real(n, o)) } as never);
    const past = st().past.length;
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    // The X arrow: the proxy went 1.3 m along X (and a hair off in Y, as three.js leaves it).
    for (const dx of [0.4, 0.9, 1.3]) {
      ctx.gizmoProxy.position.set(1 + dx, 1 + 1e-9, 1);
      ctx.fire("objectChange");
    }
    assert.deepEqual(frame.position.toArray(), [11.5, 0, 0], "drawn moved, on the grid, along X only");
    assert.deepEqual(st().groups.find((g) => g.name === "B")?.origin, [10, 0, 0], "the store waits for the release");
    ctx.fire("dragging-changed", { value: false });
    assert.equal(writes, 1, "one write for the drag");
    assert.deepEqual(st().groups.find((g) => g.name === "B")?.origin, [11.5, 0, 0]);
    assert.equal(st().past.length, past + 1, "one undo step");
    assert.equal(ctx.structureDrag, null);
    st().undo();
    assert.deepEqual(st().groups.find((g) => g.name === "B")?.origin, [10, 0, 0]);
    useCellBuilderStore.setState({ setGroupOrigin: real } as never);
  } finally {
    st().close();
  }
});

test("a structure's G-drag carries its COG frame too, so a selected equipment's COG marker follows live", () => {
  pickB();
  try {
    st().setGridStep(0.5);
    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    // A stand-in COG marker, drawn the way cogOverlay.rebuildCog does: in B's frame under cogGroup.
    const cogFrame = structureFrame(ctx.cogGroup, "B", st().groups);
    const marker = new THREE.Group();
    cogFrame.add(marker);
    st().setGizmoMode("translate");
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    ctx.gizmoProxy.position.set(1 + 1.3, 1, 1);
    ctx.fire("objectChange");
    assert.deepEqual(worldOf(marker), [12.5, 2, 3], "the COG frame moved live with the structure, mid-drag");
    ctx.fire("dragging-changed", { value: false });
  } finally {
    st().close();
  }
});

test("the structure's new origin: on the grid along the moved axes only; free with Shift held", () => {
  assert.deepEqual(movedStructureOrigin([10, 0, 0.3], [1, 1, 1], [2.3, 1.7, 1], [0], 0.5), [11.5, 0, 0.3]);
  assert.deepEqual(movedStructureOrigin([10, 0, 0.3], [1, 1, 1], [2.3, 1.7, 1], [0, 1], 0.5), [11.5, 0.5, 0.3]);
  assert.deepEqual(movedStructureOrigin([10, 0, 0], [1, 1, 1], [2.3, 1, 1], [0], 0), [11.3, 0, 0], "free move: no step");
  // The gizmo's own drag reads Shift as free move.
  pickB();
  try {
    st().setGridStep(0.5);
    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    st().setGizmoMode("translate");
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    ctx.gizmo.axis = "X";
    setFreeMove(true);
    ctx.fire("dragging-changed", { value: true });
    ctx.gizmoProxy.position.set(2.3, 1, 1);
    ctx.fire("objectChange");
    ctx.fire("dragging-changed", { value: false });
    const x = st().groups.find((g) => g.name === "B")!.origin![0];
    assert.ok(Math.abs(x - 11.3) < 1e-9, `free: ${x}`);
  } finally {
    setFreeMove(false);
    st().close();
  }
});

// --- G on a multi-selection --------------------------------------------------------------------

const { movableSet: multiMovableSet, movableCount: multiMovableCount, movableWorldCentre } = await import("@/components/viewer/cellbuilder/multiMove");
const { structureOrigin } = await import("@/utils/cellbuilder/groups");

test("2+ selected items arm G as a group, at the world centre of everything that will move", () => {
  st().open("m", "Model", 1, doc);
  try {
    const a1 = cellId("A1");
    const b1 = cellId("B1");
    st().selectItems([a1], "only");
    assert.equal(multiMovableCount(multiMovableSet(st())), 1, "one item: left to the single-item path");
    st().selectItems([b1], "add");
    const set = multiMovableSet(st());
    assert.equal(multiMovableCount(set), 2);
    // A1 [0,0,0]-[2,2,2] at A's origin [0,0,0]; B1 [0,0,0]-[2,2,2] at B's origin [10,0,0] -- the
    // world union is [0,0,0]-[12,2,2], centre (6,1,1).
    assert.deepEqual(movableWorldCentre(st(), set), [6, 1, 1]);
  } finally {
    st().close();
  }
});

test("dragging 2 selected items (different structures) moves both by the same delta, as one undo step", () => {
  st().open("m", "Model", 1, doc);
  try {
    st().setGridStep(0.5);
    const a1 = cellId("A1");
    const b1 = cellId("B1");
    st().selectItems([a1, b1], "only");
    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    st().setGizmoMode("translate");
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    assert.equal(ctx.gizmo.object, ctx.gizmoProxy, "the shared proxy is attached");
    // The proxy is a child of editFrame, which sits at the PRIMARY's structure origin (the
    // primary here is B1, last of [a1, b1] -- B's origin [10, 0, 0]), so its LOCAL position must
    // be the world centre minus that origin, not the world centre itself.
    assert.deepEqual(ctx.gizmoProxy.position.toArray(), [-4, 1, 1], "local: the world centre minus B's origin");
    assert.deepEqual(worldOf(ctx.gizmoProxy), [7, 3, 4], "world: the TRUE group centre (6,1,1) plus the model's own translation (1,2,3)");
    const past = st().past.length;
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    for (const dx of [0.4, 0.9, 1.3]) {
      ctx.gizmoProxy.position.set(-4 + dx, 1, 1);
      ctx.fire("objectChange");
    }
    // Same STORED (structure-relative) delta for both -- they started at the same local origin --
    // but that lands them at DIFFERENT world positions, each in its own structure's frame.
    assert.deepEqual(st().cells[a1].origin, [1.5, 0, 0], "A1: on the grid, X only");
    assert.deepEqual(st().cells[b1].origin, [1.5, 0, 0], "B1: the identical relative delta");
    const worldOfCell = (id: string) => {
      const c = st().cells[id]!;
      const off = structureOrigin(st().groups, c.group);
      return [c.origin[0] + off[0], c.origin[1] + off[1], c.origin[2] + off[2]];
    };
    assert.deepEqual(worldOfCell(a1), [1.5, 0, 0], "A1 in world space (A's origin is [0,0,0])");
    assert.deepEqual(worldOfCell(b1), [11.5, 0, 0], "B1 in world space -- B's own [10,0,0] still applies");
    ctx.fire("dragging-changed", { value: false });
    assert.equal(st().past.length, past + 1, "one undo step for the whole drag");
    assert.equal(ctx.multiMove, null);
    st().undo();
    assert.deepEqual(st().cells[a1].origin, [0, 0, 0]);
    assert.deepEqual(st().cells[b1].origin, [0, 0, 0]);
  } finally {
    st().close();
  }
});

test("the shared delta is quantized ONCE, so an off-grid ref keeps its exact spacing from an on-grid cell", () => {
  st().open("m", "Model", 1, doc);
  // Off the 0.5 m grid on purpose: the bug quantized each item's own ABSOLUTE start + delta,
  // which rounds an off-grid item by a different amount than an on-grid one, spreading (or
  // collapsing) a rigid group instead of moving it as one. `write` persists through `setDocExtra`
  // (as a real provider would), which is what actually keeps `st().refs` in step with every tick
  // -- the store resyncs refs whenever `docExtras` OR `cells` changes (store.ts), so a provider
  // that instead only mutated its own private state would make this test's "after" read stale
  // depending on entry order, for a reason unrelated to the fix under test.
  const START: Vec3 = [0.37, 0, 0];
  const off = registerBuilderRefs({
    owner: "shared-delta-test",
    project: (extras) => [
      { key: "r", name: "r", kindLabel: "Load", origin: (extras.sharedDeltaBox as {origin: Vec3} | undefined)?.origin ?? START, size: [1, 1, 1] as Vec3, structure: "A", edit: { move: true } },
    ],
    write: (_ref, box) => {
      st().setDocExtra("sharedDeltaBox", { origin: box.origin });
      return true;
    },
  });
  try {
    st().setGridStep(0.5);
    const a1 = cellId("A1"); // origin [0, 0, 0] -- ON the grid
    st().selectItems([a1], "only");
    st().selectItems(["shared-delta-test:r"], "add");
    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    st().setGizmoMode("translate");
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    const past = st().past.length;
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    const startCentre = [ctx.gizmoProxy.position.x, ctx.gizmoProxy.position.y, ctx.gizmoProxy.position.z];
    for (const dx of [0.4, 0.9, 1.3]) {
      ctx.gizmoProxy.position.set(startCentre[0] + dx, startCentre[1], startCentre[2]);
      ctx.fire("objectChange");
    }
    ctx.fire("dragging-changed", { value: false });
    // groupDelta.x = quantize(1.3, 0.5) = 1.5, applied IDENTICALLY to both starts.
    assert.deepEqual(st().cells[a1].origin, [1.5, 0, 0], "the on-grid cell: start (0) + the shared delta");
    const refX = st().refs["shared-delta-test:r"].origin[0];
    assert.ok(Math.abs(refX - 1.87) < 1e-9, `the off-grid ref keeps its own 0.37 start plus the SAME delta (1.87), got ${refX}`);
    assert.ok(Math.abs(refX - st().cells[a1].origin[0] - 0.37) < 1e-9, "the 0.37 spacing between them survives the drag");
    assert.equal(st().past.length, past + 1, "one undo step");
  } finally {
    off();
    st().close();
  }
});

test("R/S on a multi-selection do nothing (single-item only); the HUD gets a hint instead", () => {
  st().open("m", "Model", 1, doc);
  try {
    const a1 = cellId("A1");
    const b1 = cellId("B1");
    st().selectItems([a1, b1], "only");
    const ctx = gizmoCtx();
    onKeyDown(ctx, key("r"));
    assert.equal(st().gizmoMode, "none", "rotate never arms");
    assert.equal(st().toolHint, ROTATE_SCALE_HINT);
    onKeyDown(ctx, key("s"));
    assert.equal(st().gizmoMode, "none", "resize never arms either");
  } finally {
    st().close();
  }
});

// The hint above is never cleared by keyboard.ts itself -- initScene.ts's store subscription
// does it, on the next selection or gizmo-mode change. clearsRotateScaleHint is the pure decision
// that subscription makes; it is exercised directly since mounting the whole scene is not.
test("clearsRotateScaleHint fires only for the rotate/scale hint, and only on a selection or gizmo-mode change", () => {
  assert.equal(clearsRotateScaleHint(ROTATE_SCALE_HINT, true, false), true, "selection changed");
  assert.equal(clearsRotateScaleHint(ROTATE_SCALE_HINT, false, true), true, "gizmo mode changed");
  assert.equal(clearsRotateScaleHint(ROTATE_SCALE_HINT, false, false), false, "neither changed: still showing, left alone");
  assert.equal(clearsRotateScaleHint("Extrude 1.2 m — type depth, ↵ commit, Esc cancel", true, false), false, "a different hint is untouched");
  assert.equal(clearsRotateScaleHint(null, true, true), false);
});

test("G arms the multi gizmo for a selected space plus its own (also selected) equipment, not the single-item path", () => {
  st().open("m", "Model", 1, doc);
  try {
    const a1 = cellId("A1");
    const eq = cellId("A1-EQ");
    st().selectItems([a1, eq], "only"); // the equipment is the PRIMARY (last clicked)
    assert.equal(multiMovableCount(multiMovableSet(st())), 1, "the equipment rides for free; only A1 is independently movable");
    const ctx = gizmoCtx();
    onKeyDown(ctx, key("g"));
    assert.equal(st().gizmoMode, "translate", "G arms the multi path (the old bug fell back to moving only the primary, the equipment)");
  } finally {
    st().close();
  }
});

test("G ARMS the multi gizmo -- a second G does not toggle it back off", () => {
  st().open("m", "Model", 1, doc);
  try {
    const a1 = cellId("A1");
    const b1 = cellId("B1");
    st().selectItems([a1, b1], "only");
    const ctx = gizmoCtx();
    onKeyDown(ctx, key("g"));
    assert.equal(st().gizmoMode, "translate", "first G arms");
    onKeyDown(ctx, key("g"));
    assert.equal(st().gizmoMode, "translate", "second G stays armed -- it does not disarm (the old bug toggled it off)");
  } finally {
    st().close();
  }
});

test("a global opening re-homes against its space's OWN-TICK position, not the previous tick's", () => {
  st().open("m", "Model", 1, doc);
  try {
    st().setGridStep(0.5);
    const a1 = cellId("A1"); // origin [0, 0, 0] size [2, 2, 2]
    const door = cellId("A1-DOOR"); // origin [1.9, 0.8, 0.8] size [0.2, 0.4, 0.4]: straddles A1's +X face
    // Select the door FIRST, then add A1: in selectedCellIds order (the bug's order, without the
    // space-first sort) the door's own updateCell would run before A1's, re-homing it against A1's
    // STALE (start-of-drag) box instead of where A1 actually lands this tick.
    st().selectItems([door], "only");
    st().selectItems([a1], "add");
    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    st().setGizmoMode("translate");
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    const startCentre = [ctx.gizmoProxy.position.x, ctx.gizmoProxy.position.y, ctx.gizmoProxy.position.z];
    // One big jump (not stepped): against A1's STALE box the door's new centre (x=7) would be
    // nowhere near A1's old +X face (x=2) -- far outside nearestFaceStraddled's tolerance -- so
    // the bug clears SPACE_NAME/SPACE_SIDE outright, rather than merely rounding differently.
    ctx.gizmoProxy.position.set(startCentre[0] + 5, startCentre[1], startCentre[2]);
    ctx.fire("objectChange");
    ctx.fire("dragging-changed", { value: false });
    assert.deepEqual(st().cells[a1].origin, [5, 0, 0], "A1 moved");
    assert.deepEqual(st().cells[door].origin, [6.9, 0.8, 0.8], "the door moved by the same delta");
    assert.equal(st().cells[door].params.SPACE_NAME, "A1", "still re-homed to A1 (its moved, CURRENT box)");
    assert.equal(st().cells[door].params.SPACE_SIDE, "X", "+X face, SE/SIDE label without the leading +");
  } finally {
    st().close();
  }
});

// --- Shift+D on a space with equipment, then dragging the copy -------------------------------

const { equipContainedIn } = await import("@/components/viewer/cellbuilder/cellGizmo");

test("single path: dragging a just-placed duplicate does not take the original space's equipment with it", () => {
  st().open("m", "Model", 1, doc);
  try {
    const a1 = cellId("A1");
    const eq = cellId("A1-EQ");
    st().setGizmoVertexSnap(false); // this ctx has no pointer for the corner-snap check
    st().selectItems([a1], "only");
    const copies = st().duplicateSelection();
    assert.ok(copies && copies.length === 1, "A1's equipment was not selected, so only A1 is copied");
    const copyA1 = copies![0];
    assert.deepEqual(st().selectedCellIds, [copyA1]);
    assert.deepEqual(st().placingCopyIds, [copyA1]);

    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    syncStructureFrames(ctx);
    syncGizmo(ctx); // seeds gizmoProxy at the copy's (= the original's) box centre
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    // The exact rule under test (cellGizmo.ts's equipContainedIn): while placingCopyIds names
    // the just-placed copy, only equipment that is ITSELF a copy may ride along.
    assert.deepEqual(ctx.translateEquip, [], "the ORIGINAL equipment is not captured as a rider");
    for (const dx of [0.4, 0.9, 1.3]) {
      ctx.gizmoProxy.position.set(1 + dx, 1, 1);
      ctx.fire("objectChange");
    }
    ctx.fire("dragging-changed", { value: false });
    assert.equal(st().placingCopyIds, null, "cleared once the placing drag ends");
    assert.deepEqual(st().cells[copyA1].origin, [1.5, 0, 0], "the copy moved");
    assert.deepEqual(st().cells[a1].origin, [0, 0, 0], "the original space stayed");
    assert.deepEqual(st().cells[eq].origin, [0.5, 0.5, 0], "the original's equipment did NOT move");
  } finally {
    st().close();
  }
});

test("multi path: dragging two just-placed duplicates does not take either original's equipment with it", () => {
  st().open("m", "Model", 1, doc);
  try {
    const a1 = cellId("A1");
    const b1 = cellId("B1");
    const eqA = cellId("A1-EQ");
    const eqB = cellId("B1-EQ");
    st().selectItems([a1, b1], "only");
    const copies = st().duplicateSelection();
    assert.ok(copies && copies.length === 2, "neither space's equipment was selected, so only the 2 spaces are copied");
    assert.deepEqual([...st().selectedCellIds].sort(), [...copies!].sort());
    assert.deepEqual([...st().placingCopyIds!].sort(), [...copies!].sort());

    const ctx = gizmoCtx();
    installCellGizmo(ctx);
    st().setGizmoMode("translate"); // duplicateSelection already armed it; re-assert explicitly
    syncStructureFrames(ctx);
    syncGizmo(ctx);
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    assert.ok(ctx.multiMove, "the multi path armed (2+ movable items)");
    const riderIds = ctx.multiMove!.entries.filter((e: { kind: string }) => e.kind === "cell").map((e: { id: string }) => e.id);
    assert.ok(!riderIds.includes(eqA) && !riderIds.includes(eqB), "neither ORIGINAL equipment rides along");
    for (const dx of [0.4, 0.9, 1.3]) {
      ctx.gizmoProxy.position.set(6 + dx, 1, 1);
      ctx.fire("objectChange");
    }
    ctx.fire("dragging-changed", { value: false });
    assert.equal(st().placingCopyIds, null, "cleared once the placing drag ends");
    assert.deepEqual(st().cells[a1].origin, [0, 0, 0], "original A1 stayed");
    assert.deepEqual(st().cells[b1].origin, [0, 0, 0], "original B1 stayed");
    assert.deepEqual(st().cells[eqA].origin, [0.5, 0.5, 0], "A1's original equipment did not move");
    assert.deepEqual(st().cells[eqB].origin, [0.5, 0.5, 0], "B1's original equipment did not move");
  } finally {
    st().close();
  }
});
