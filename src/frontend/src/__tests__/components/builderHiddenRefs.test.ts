// Hidden refs: a plugin's ref (a load, a modification, a support) can be hidden and
// shown exactly like a cell -- H / Shift+H / Unhide all, the Properties Hide button and the Model
// panel's eye all end up calling the same `hideCells` / `unhideCells` / `unhideAllCells`, which now
// file an id under `hiddenCellIds` or `hiddenRefIds` by what it actually names. This covers the
// store-level classification and the scene-level effect (the ref's mesh goes invisible AND
// unpickable, as a hidden cell's box already does).

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { rebuildRefs, applyRefVisibility } = await import("@/components/viewer/cellbuilder/refsOverlay");
const { pickRefMesh, setPointer } = await import("@/components/viewer/cellbuilder/pointerTools");
const { onKeyDown } = await import("@/components/viewer/cellbuilder/keyboard");
const { setShellModes } = await import("@/layout/shellModes");
type Ctx = Parameters<typeof pickRefMesh>[0];
type Vec3 = [number, number, number];

const st = () => useCellBuilderStore.getState();
const REF = "hide-test:r";
const START = { origin: [0.03, 0.07, 0.11] as Vec3, size: [2, 1, 1] as Vec3 };

const doc = { schema_version: "1.1", grid: {}, blueprint: {}, spaces: [], equipments: [], boxes: [{ ...START }] } as unknown as ProceduralDoc;

/** Enough of the builder scene for a ref pick: a camera looking along +Y at the ref, on a 100x100
 *  view (same framing builderRefsScene.test.ts uses). */
function makeCtx(): Ctx {
  const camera = new THREE.PerspectiveCamera(90, 1, 0.1, 100);
  camera.up.set(0, 0, 1);
  camera.position.set(1.03, -10, 0.61);
  camera.lookAt(1.03, 0.57, 0.61);
  camera.updateMatrixWorld(true);
  const el = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) };
  const refsGroup = new THREE.Group();
  return {
    host: { camera: () => camera, input: () => el, rect: () => el.getBoundingClientRect() },
    raycaster: new THREE.Raycaster(),
    pointer: new THREE.Vector2(),
    refsGroup,
    refMeshById: new Map(),
  } as unknown as Ctx;
}

const ev = (x: number, y = 50) => ({ clientX: x, clientY: y } as unknown as PointerEvent);
/** The pointer's x on screen for a point at model x on the ref's middle line (mirrors builderRefsScene). */
const screenX = (x: number) => ((x - 1.03) / 10.57 + 1) * 50;

function withRef(run: (ctx: Ctx) => void): void {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "hide-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b) => ({
        key: "r",
        name: "r",
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
        edit: { move: true, resize: true },
      })),
  });
  const ctx = makeCtx();
  rebuildRefs(ctx);
  ctx.refsGroup.updateMatrixWorld(true);
  try {
    run(ctx);
  } finally {
    off();
    st().close();
  }
}

test("hideCells files a cell id under hiddenCellIds and a ref id under hiddenRefIds", () => {
  withRef(() => {
    st().setSelection({ kind: "cell", cellId: "nonexistent" }); // a no-op pick, unaffected below
    st().hideCells([REF]);
    assert.deepEqual(st().hiddenRefIds, [REF]);
    assert.deepEqual(st().hiddenCellIds, []);
  });
});

test("hiding the picked ref clears the selection, as hiding the picked cell already does", () => {
  withRef(() => {
    st().setSelection({ kind: "ref", refId: REF });
    st().hideCells([REF]);
    assert.equal(st().selection, null);
    assert.equal(st().gizmoMode, "none");
  });
});

test("unhideCells and unhideAllCells both reach hiddenRefIds", () => {
  withRef(() => {
    st().hideCells([REF]);
    st().unhideCells([REF]);
    assert.deepEqual(st().hiddenRefIds, []);
    st().hideCells([REF]);
    st().unhideAllCells();
    assert.deepEqual(st().hiddenRefIds, []);
  });
});

test("a ref that leaves `refs` drops out of hiddenRefIds on the next refreshRefs", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "hide-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b) => ({
        key: "r", name: "r", kindLabel: "Box", origin: b.origin, size: b.size,
      })),
  });
  try {
    st().hideCells([REF]);
    assert.deepEqual(st().hiddenRefIds, [REF]);
    // The provider now projects nothing: the ref is gone from `refs`.
    off();
    st().refreshRefs();
    assert.deepEqual(st().hiddenRefIds, [], "the stale id is dropped, not left stranded forever");
  } finally {
    st().close();
  }
});

test("a hidden ref's mesh is invisible and pickRefMesh skips it, falling through", () => {
  withRef((ctx) => {
    assert.equal(ctx.refMeshById.size, 1);
    setPointer(ctx, ev(screenX(1.2)));
    assert.ok(pickRefMesh(ctx), "visible: picked");

    st().hideCells([REF]);
    applyRefVisibility(ctx);
    assert.equal(ctx.refMeshById.get(REF)?.visible, false);
    setPointer(ctx, ev(screenX(1.2)));
    assert.equal(pickRefMesh(ctx), null, "hidden: the click falls through");

    st().unhideCells([REF]);
    applyRefVisibility(ctx);
    assert.equal(ctx.refMeshById.get(REF)?.visible, true);
    setPointer(ctx, ev(screenX(1.2)));
    assert.ok(pickRefMesh(ctx), "shown again: picked once more");
  });
});

test("rebuildRefs itself applies the current hide state to the freshly-built meshes", () => {
  withRef((ctx) => {
    st().hideCells([REF]);
    rebuildRefs(ctx); // a rebuild (e.g. an include-view change) must not un-hide it
    assert.equal(ctx.refMeshById.get(REF)?.visible, false);
  });
});

// Shift+H while comparing: a lingering Input pick must not hide a builder item while the
// shell is showing a scene pick instead (Output, Fabrication) -- the key should fall through
// unclaimed to the global mesh-range hide.
test("Shift+H skips the builder hide while the mode draws the builder only to COMPARE", () => {
  withRef(() => {
    st().setSelection({ kind: "ref", refId: REF });
    const keyCtx = { numEntry: null, placeEntry: null, equipEntry: null, openEntry: null, modalMove: null, loftActive: null } as never;
    const shiftH = () => ({ key: "H", code: "KeyH", shiftKey: true, ctrlKey: false, metaKey: false, altKey: false, target: null, preventDefault() {}, stopPropagation() {} }) as unknown as KeyboardEvent;
    try {
      setShellModes([{ id: "output", label: "Output", order: 0, behaviour: { builder: "compare" } }], "output");
      onKeyDown(keyCtx, shiftH());
      assert.deepEqual(st().hiddenRefIds, [], "compare: not hidden, left for the global handler");

      setShellModes([{ id: "input", label: "Input", order: 0, behaviour: { builder: "edit" } }], "input");
      onKeyDown(keyCtx, shiftH());
      assert.deepEqual(st().hiddenRefIds, [REF], "edit: hidden as before");
    } finally {
      setShellModes([], null);
      st().unhideCells([REF]);
    }
  });
});
