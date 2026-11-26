// A plugin's ref in the builder's scene: a click picks it, the move gizmo moves it and a face drag
// resizes it -- snapped by the cells' rules (only the dragged axes move and snap; the left Shift
// key moves freely) -- and each result reaches the provider's `write`, one undo step per drag.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { setShellMode, setShellModes } = await import("@/layout/shellModes");
const { onPointerDown, onPointerMove, onPointerUp, pickBuilderMesh, pickRefMesh, setPointer, startFaceDrag } = await import("@/components/viewer/cellbuilder/pointerTools");
const { installCellGizmo } = await import("@/components/viewer/cellbuilder/cellGizmo");
const { rebuildRefs, refreshRefStyles } = await import("@/components/viewer/cellbuilder/refsOverlay");
const { REF_OPACITY, REF_SELECTED_OPACITY } = await import("@/components/viewer/cellbuilder/sceneConstants");
const { boxTarget } = await import("@/components/viewer/cellbuilder/gizmoBox");
const { movedOrigin, quantize } = await import("@/utils/cellbuilder/snap");
const { setFreeMove } = await import("@/utils/gizmo/freeMove");
type Ctx = Parameters<typeof onPointerDown>[0];
type Vec3 = [number, number, number];

const st = () => useCellBuilderStore.getState();
const REF = "scene-test:r";
const START = { origin: [0.03, 0.07, 0.11] as Vec3, size: [2, 1, 1] as Vec3 };
const writes: { origin: Vec3; size: Vec3 }[] = [];

const doc = { schema_version: "1.1", grid: {}, blueprint: {}, spaces: [], equipments: [], boxes: [{ ...START }] } as unknown as ProceduralDoc;

/** Enough of the builder scene for the pointer and gizmo paths: a camera looking along +Y at
 *  the ref, +X to the right, on a 100 x 100 px view. */
function makeCtx(): Ctx & { fire: (type: string, e?: object) => void } {
  const camera = new THREE.PerspectiveCamera(90, 1, 0.1, 100);
  camera.up.set(0, 0, 1);
  camera.position.set(1.03, -10, 0.61);
  camera.lookAt(1.03, 0.57, 0.61);
  camera.updateMatrixWorld(true);
  const el = {
    parentElement: null,
    style: {} as Record<string, string>,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
    setPointerCapture() {},
    releasePointerCapture() {},
  };
  const listeners = new Map<string, ((e: unknown) => void)[]>();
  const gizmo = {
    axis: null as string | null,
    object: null,
    dragging: false,
    addEventListener: (t: string, l: (e: unknown) => void) => listeners.set(t, [...(listeners.get(t) ?? []), l]),
  };
  const refsGroup = new THREE.Group();
  const ctx = {
    // The viewport the builder is drawn in (cellbuilder/viewHost.ts): its camera and its input element.
    host: { camera: () => camera, input: () => el, rect: () => el.getBoundingClientRect(), setCameraEnabled() {} },
    raycaster: new THREE.Raycaster(),
    pointer: new THREE.Vector2(),
    container: new THREE.Group(),
    // The structure frames (cellbuilder/structureFrames.ts): no structure has an origin here.
    editFrame: new THREE.Group(),
    placeFrame: new THREE.Group(),
    cellsGroup: new THREE.Group(),
    meshById: new Map(),
    refsGroup,
    refMeshById: new Map(),
    resizeGroup: new THREE.Group(),
    moveMeasureGroup: new THREE.Group(),
    gizmo,
    gizmoProxy: new THREE.Object3D(),
    portGizmo: { object: null, axis: null },
    snapMarker: { visible: false, position: new THREE.Vector3() },
    drag: null,
    modalMove: null,
    moveMeasure: null,
    pendingSelect: null,
    pendingGizmoExit: null,
    rightPress: null,
    longPressTimer: null,
    translateEquip: [],
    loftDragLast: null,
    placeEntry: null,
    fire: (type: string, e: object = {}) => (listeners.get(type) ?? []).forEach((l) => l(e)),
  };
  return ctx as unknown as Ctx & { fire: (type: string, e?: object) => void };
}

const ev = (x: number, y = 50) =>
  ({ button: 0, clientX: x, clientY: y, pointerId: 1, pointerType: "mouse", stopPropagation() {}, preventDefault() {} }) as unknown as PointerEvent;
/** The pointer's x on screen for a point at model x `x` on the ref's middle line (90 deg view, 10.57 m away). */
const screenX = (x: number) => ((x - 1.03) / 10.57 + 1) * 50;

function withRef(run: (ctx: ReturnType<typeof makeCtx>) => void): void {
  st().open("m", "Model", 1, doc);
  st().setGridStep(0.25);
  st().setGizmoVertexSnap(false);
  writes.length = 0;
  const off = registerBuilderRefs({
    owner: "scene-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b) => ({
        key: "r",
        name: "r",
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
        edit: { move: true, resize: true },
      })),
    write: (_ref, box) => {
      writes.push(box);
      st().setDocExtra("boxes", [box]);
      return true;
    },
  });
  const ctx = makeCtx();
  rebuildRefs(ctx);
  ctx.refsGroup.updateMatrixWorld(true);
  try {
    run(ctx);
  } finally {
    setFreeMove(false);
    off();
    st().close();
  }
}

test("a click on a ref picks it", () => {
  withRef((ctx) => {
    assert.equal(ctx.refMeshById.size, 1, "the ref is drawn");
    onPointerDown(ctx, ev(50));
    onPointerUp(ctx, ev(50));
    assert.deepEqual(st().selection, { kind: "ref", refId: REF });
    // A click beside it picks nothing new.
    st().setSelection(null);
    onPointerDown(ctx, ev(2));
    onPointerUp(ctx, ev(2));
    assert.equal(st().selection, null);
  });
});

test("a Ctrl/Cmd/Shift click on a ref toggles it into the multi-select set instead of replacing", () => {
  withRef((ctx) => {
    const additive = (x: number) =>
      ({ button: 0, clientX: x, clientY: 50, pointerId: 1, pointerType: "mouse", ctrlKey: true, stopPropagation() {}, preventDefault() {} }) as unknown as PointerEvent;
    onPointerDown(ctx, additive(50));
    onPointerUp(ctx, additive(50));
    assert.deepEqual(st().selectedRefIds, [REF], "an additive click toggles it IN");
    assert.deepEqual(st().selection, { kind: "ref", refId: REF });
    onPointerDown(ctx, additive(50));
    onPointerUp(ctx, additive(50));
    assert.deepEqual(st().selectedRefIds, [], "a second additive click toggles it back OUT");
    assert.equal(st().selection, null, "emptying the set clears the selection");
  });
});

test("a mode that draws the builder only to COMPARE does not pick it: the press falls through, unclaimed", () => {
  withRef((ctx) => {
    setShellModes([{ id: "output", label: "Output", order: 0, behaviour: { builder: "compare" } }], "output");
    try {
      let stopped = false;
      const down = { button: 0, clientX: 50, clientY: 50, pointerId: 1, pointerType: "mouse", stopPropagation: () => void (stopped = true), preventDefault() {} } as unknown as PointerEvent;
      onPointerDown(ctx, down);
      onPointerUp(ctx, ev(50));
      assert.equal(st().selection, null, "the ref is not picked while comparing");
      assert.equal(stopped, false, "not claimed: the scene's own click handler still sees it");
    } finally {
      setShellModes([], null);
    }
  });
});

test("'edit' presence (no modes, or a mode that declares it) still picks the ref as before", () => {
  withRef((ctx) => {
    setShellModes([{ id: "input", label: "Input", order: 0, behaviour: { builder: "edit" } }], "input");
    try {
      onPointerDown(ctx, ev(50));
      onPointerUp(ctx, ev(50));
      assert.deepEqual(st().selection, { kind: "ref", refId: REF });
    } finally {
      setShellModes([], null);
    }
  });
});

// The compare-mode click-through used to be press-select's own guard; it is now inside
// pickBuilderMesh/pickRefMesh themselves, so every caller gets it at once -- the hover cursor,
// the long-press/right-click menus and the gizmo's "over empty space" check (pointerTools.ts),
// not only a plain press. Pinning it here, on the two functions directly, covers all of them.
test("pickBuilderMesh and pickRefMesh themselves return null while comparing, whatever the caller", () => {
  withRef((ctx) => {
    setPointer(ctx, ev(50));
    setShellModes([{ id: "output", label: "Output", order: 0, behaviour: { builder: "compare" } }], "output");
    try {
      assert.equal(pickRefMesh(ctx), null, "comparing: no ref hit even directly");
      assert.equal(pickBuilderMesh(ctx), null, "comparing: no cell hit either");
      setShellModes([{ id: "input", label: "Input", order: 0, behaviour: { builder: "edit" } }], "input");
      assert.ok(pickRefMesh(ctx), "edit presence: picks again");
    } finally {
      setShellModes([], null);
    }
  });
});

test("the move gizmo moves a ref along the dragged axis only, snapped to the grid, as one undo step", () => {
  withRef((ctx) => {
    installCellGizmo(ctx);
    st().setSelection({ kind: "ref", refId: REF });
    st().setGizmoMode("translate");
    const past = st().past.length;
    ctx.gizmo.axis = "X";
    ctx.fire("dragging-changed", { value: true });
    // The X arrow dragged the centre to x = 1.4; three.js left y and z a hair off (float noise).
    const center: Vec3 = [1.4, 0.57 + 1e-9, 0.61 - 1e-9];
    for (const x of [1.2, 1.4]) {
      ctx.gizmoProxy.position.set(x, center[1], center[2]);
      ctx.fire("objectChange");
    }
    ctx.fire("dragging-changed", { value: false });
    const want = movedOrigin(START.origin, START.size, center, [0], 0.25);
    assert.deepEqual(st().refs[REF].origin, want);
    assert.equal(want[1], 0.07, "Y untouched, off the grid as it was");
    assert.equal(want[2], 0.11, "Z untouched");
    assert.deepEqual(writes[writes.length - 1], { origin: want, size: START.size }, "reached the provider's write");
    assert.equal(st().past.length, past + 1, "one undo step for the drag");
    st().undo();
    assert.deepEqual(st().refs[REF].origin, START.origin);
  });
});

test("Shift moves a ref freely: no grid step", () => {
  withRef((ctx) => {
    installCellGizmo(ctx);
    st().setSelection({ kind: "ref", refId: REF });
    st().setGizmoMode("translate");
    ctx.gizmo.axis = "X";
    setFreeMove(true);
    ctx.fire("dragging-changed", { value: true });
    ctx.gizmoProxy.position.set(1.4, 0.57, 0.61);
    ctx.fire("objectChange");
    ctx.fire("dragging-changed", { value: false });
    assert.ok(Math.abs(st().refs[REF].origin[0] - 0.4) < 1e-9, `free: ${st().refs[REF].origin[0]}`);
  });
});

test("a face drag resizes a ref: only the dragged face moves, by whole grid steps, as one undo step", () => {
  withRef((ctx) => {
    st().setSelection({ kind: "ref", refId: REF });
    st().setGizmoMode("resize");
    const past = st().past.length;
    const down = ev(screenX(1.2));
    setPointer(ctx, down);
    assert.equal(startFaceDrag(ctx, boxTarget(st(), REF, true)!, 0, down, true), true, "the +X face is taken");
    onPointerMove(ctx, ev(screenX(1.57)));
    onPointerUp(ctx, ev(screenX(1.57)));
    const r = st().refs[REF];
    assert.deepEqual(r.origin, START.origin, "the -X face, and Y and Z, stay");
    assert.ok(Math.abs(r.size[0] - (2 + quantize(0.37, 0.25))) < 1e-6, `size ${r.size[0]}`);
    assert.deepEqual(r.size.slice(1), [1, 1]);
    assert.equal(writes.length > 0, true, "reached the provider's write");
    assert.equal(st().past.length, past + 1, "one undo step for the drag");
    assert.equal(st().mode, "idle");
  });
});

test("a ref its plugin does not let move or resize takes no gizmo drag", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "fixed",
    project: () => [{ key: "f", name: "f", kindLabel: "Box", origin: [0, 0, 0], size: [1, 1, 1] }],
    write: () => true,
  });
  try {
    const ctx = makeCtx();
    const box = boxTarget(st(), "fixed:f", true)!;
    setPointer(ctx, ev(50));
    assert.equal(startFaceDrag(ctx, box, 0, ev(50), true), false);
  } finally {
    off();
    st().close();
  }
});

test("a look:\"none\" ref makes no mesh, but stays in the store and is still pickable through it", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "none-test",
    project: () => [
      { key: "shown", name: "shown", kindLabel: "Box", origin: [0, 0, 0], size: [1, 1, 1] },
      { key: "hidden", name: "hidden", kindLabel: "Box", origin: [5, 5, 5], size: [1, 1, 1], look: "none" },
    ],
  });
  try {
    const ctx = makeCtx();
    rebuildRefs(ctx);
    // No mesh for the "none" ref -- it is not drawn, so it can not be picked or framed in the
    // scene -- but the normal-look ref beside it is drawn as before.
    assert.equal(ctx.refMeshById.size, 1, "only the drawn ref gets a mesh");
    assert.equal(ctx.refMeshById.has("none-test:hidden"), false);
    assert.equal(ctx.refMeshById.has("none-test:shown"), true);
    // Still in the store -- the model tree and Properties read refs from here, not from the
    // scene -- so it can be picked from there (a click in the 3D view has nothing to hit).
    const hidden = st().refs["none-test:hidden"];
    assert.ok(hidden, "the none ref is still projected");
    assert.equal(hidden.look, "none");
    st().setSelection({ kind: "ref", refId: "none-test:hidden" });
    assert.deepEqual(st().selection, { kind: "ref", refId: "none-test:hidden" });
    // Re-styling and rebuilding tolerate the missing mesh (refreshRefStyles only visits what's
    // in refMeshById; a selected-but-undrawn ref throws nothing).
    assert.doesNotThrow(() => rebuildRefs(ctx));
  } finally {
    off();
    st().close();
  }
});

// Every ref in a multi-selection lights up in 3D, not only the primary.
test("refreshRefStyles highlights every ref in selectedRefIds, not only the primary selection", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "multi-test",
    project: () => [
      { key: "a", name: "a", kindLabel: "Box", origin: [0, 0, 0], size: [1, 1, 1] },
      { key: "b", name: "b", kindLabel: "Box", origin: [5, 5, 5], size: [1, 1, 1] },
    ],
  });
  try {
    const ctx = makeCtx();
    rebuildRefs(ctx);
    const opacityOf = (id: string) => (ctx.refMeshById.get(id)!.material as THREE.MeshBasicMaterial[])[0].opacity;
    // Nothing selected: both at the plain opacity.
    assert.equal(opacityOf("multi-test:a"), REF_OPACITY);
    assert.equal(opacityOf("multi-test:b"), REF_OPACITY);
    // "a" is the primary (selection), "b" only in selectedRefIds (Ctrl-clicked alongside it, or a
    // box select): both must read as selected, not only the primary.
    st().setSelection({ kind: "ref", refId: "multi-test:a" });
    useCellBuilderStore.setState({ selectedRefIds: ["multi-test:a", "multi-test:b"] });
    refreshRefStyles(ctx);
    assert.equal(opacityOf("multi-test:a"), REF_SELECTED_OPACITY, "the primary");
    assert.equal(opacityOf("multi-test:b"), REF_SELECTED_OPACITY, "selected but not primary");
  } finally {
    off();
    st().close();
  }
});
