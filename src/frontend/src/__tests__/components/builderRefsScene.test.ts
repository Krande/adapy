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
const { onPointerDown, onPointerMove, onPointerUp, setPointer, startFaceDrag } = await import("@/components/viewer/cellbuilder/pointerTools");
const { installCellGizmo } = await import("@/components/viewer/cellbuilder/cellGizmo");
const { rebuildRefs } = await import("@/components/viewer/cellbuilder/refsOverlay");
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
    renderer: { domElement: el },
    camera,
    raycaster: new THREE.Raycaster(),
    pointer: new THREE.Vector2(),
    container: new THREE.Group(),
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
