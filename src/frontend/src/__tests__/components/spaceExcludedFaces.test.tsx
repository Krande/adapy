// A space's excluded side (SEi, or the SE list) is not drawn, and a click on it
// falls through to whatever is behind it. Box materials are DoubleSide (the default
// one-sided FrontSide used to cull a box's OWN far face from a straight-through ray, so excluding
// a near face uselessly showed nothing behind it on the inside, or whatever sat further along the
// ray): with the near face gone, the ray now meets the box's own inner far face first -- unless
// that is excluded too, in which case it keeps going to a second space further along the ray, or,
// with nothing there, the scene (null). The edge outline is untouched (not asserted here:
// refreshFaceStyles never touches it, only the per-face materials).

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { rebuild } = await import("@/components/viewer/cellbuilder/meshRebuild");
const { pickBuilderMesh, setPointer } = await import("@/components/viewer/cellbuilder/pointerTools");
const { BOX_FACE_SIDES } = await import("@/utils/cellbuilder/snap");

const st = () => useCellBuilderStore.getState();

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "A1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 }],
  equipments: [],
} as unknown as ProceduralDoc;

// A second space further along the ray, for the "falls through to what is behind it" case.
const docWithTwo = {
  ...doc,
  spaces: [...doc.spaces, { NAME: "A2", X: 0, Y: 5, Z: 0, DX: 2, DY: 2, DZ: 2 }],
} as unknown as ProceduralDoc;

/** A camera looking along +Y at the box's centre (1, 1, 1): it meets the -Y face first, then
 *  (were it to pass through) the +Y face. */
function makeCtx() {
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
  camera.up.set(0, 0, 1);
  camera.position.set(1, -10, 1);
  camera.lookAt(1, 1, 1);
  camera.updateMatrixWorld(true);
  const el = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) };
  return {
    host: { camera: () => camera, input: () => el, rect: () => el.getBoundingClientRect() },
    raycaster: new THREE.Raycaster(),
    pointer: new THREE.Vector2(),
    cellsGroup: new THREE.Group(),
    refsGroup: new THREE.Group(), // pickRefMesh (called by pickBuilderMesh's caller chain) reads this
    refMeshById: new Map(),
    meshById: new Map(),
    cadPreviewShown: new Set<string>(),
    cadPreviewCache: new Map(),
    hovered: null,
    hoveredEdge: null,
    ghost: { visible: false },
    ghostBox: null,
    selectedEdgeLine: { visible: false },
    hoverEdgeLine: { visible: false },
    faceOverlay: { visible: false },
  } as unknown as Parameters<typeof rebuild>[0];
}

function withSpace(run: (ctx: ReturnType<typeof makeCtx>, cellId: string) => void, model: ProceduralDoc = doc): void {
  st().open("m", "Model", 1, model);
  const ctx = makeCtx();
  rebuild(ctx);
  ctx.cellsGroup.updateMatrixWorld(true);
  const cellId = Object.values(st().cells).find((c) => c.name === "A1")!.id;
  try {
    run(ctx, cellId);
  } finally {
    st().close();
  }
}

// se=2 is -Y (BOX_FACE_SIDES: {axis:1, positive:false, se:2}), the face the camera meets first;
// se=3 is +Y, its own far face along that same ray.
const NEG_Y_MATERIAL_INDEX = BOX_FACE_SIDES.findIndex((s) => s.se === 2);
const POS_Y_MATERIAL_INDEX = BOX_FACE_SIDES.findIndex((s) => s.se === 3);

test("a space's box face materials are DoubleSide", () => {
  withSpace((ctx, cellId) => {
    const mats = ctx.meshById.get(cellId)!.material as THREE.Material[];
    for (let i = 0; i < mats.length; i++) assert.equal(mats[i].side, THREE.DoubleSide, `material ${i}`);
  });
});

test("excluding -Y (SE2) hides that face's material; the others stay visible", () => {
  withSpace((ctx, cellId) => {
    st().setCellParam(cellId, "SE2", true);
    rebuild(ctx); // materials are re-created wholesale on every rebuild; must re-apply every time
    const mesh = ctx.meshById.get(cellId)!;
    const mats = mesh.material as THREE.Material[];
    assert.equal(mats[NEG_Y_MATERIAL_INDEX].visible, false);
    for (let i = 0; i < mats.length; i++) if (i !== NEG_Y_MATERIAL_INDEX) assert.equal(mats[i].visible, true, `material ${i}`);
  });
});

test("unchecking it again (reload) restores the face", () => {
  withSpace((ctx, cellId) => {
    st().setCellParam(cellId, "SE2", true);
    rebuild(ctx);
    st().setCellParam(cellId, "SE2", null);
    rebuild(ctx);
    const mesh = ctx.meshById.get(cellId)!;
    assert.equal((mesh.material as THREE.Material[])[NEG_Y_MATERIAL_INDEX].visible, true);
  });
});

test("a click through the excluded near face now hits the box's own inner far face", () => {
  withSpace((ctx, cellId) => {
    setPointer(ctx, { clientX: 50, clientY: 50 } as unknown as PointerEvent);
    const before = pickBuilderMesh(ctx);
    assert.equal(before?.object, ctx.meshById.get(cellId), "normally A1's near (-Y) face");
    assert.equal(before?.face?.materialIndex, NEG_Y_MATERIAL_INDEX);

    st().setCellParam(cellId, "SE2", true);
    rebuild(ctx); // fresh meshes every rebuild; their matrixWorld needs recomputing before a raycast
    ctx.cellsGroup.updateMatrixWorld(true);
    setPointer(ctx, { clientX: 50, clientY: 50 } as unknown as PointerEvent);
    const through = pickBuilderMesh(ctx);
    assert.equal(through?.object, ctx.meshById.get(cellId), "still A1 -- its own +Y face, seen from inside");
    assert.equal(through?.face?.materialIndex, POS_Y_MATERIAL_INDEX);
  });
});

test("excluding both faces along the ray falls through to a second space further along it", () => {
  withSpace(
    (ctx, cellId) => {
      st().setCellParam(cellId, "SE2", true);
      st().setCellParam(cellId, "SE3", true);
      rebuild(ctx);
      ctx.cellsGroup.updateMatrixWorld(true);
      setPointer(ctx, { clientX: 50, clientY: 50 } as unknown as PointerEvent);
      const through = pickBuilderMesh(ctx);
      const a2 = Object.values(st().cells).find((c) => c.name === "A2")!;
      assert.equal(through?.object, ctx.meshById.get(a2.id), "A2's -Y face, A1 having nothing left to hit");
      assert.equal(through?.face?.materialIndex, NEG_Y_MATERIAL_INDEX);
    },
    docWithTwo,
  );
});

test("excluding every side leaves nothing to pick: the click falls through to the scene", () => {
  withSpace((ctx, cellId) => {
    for (let se = 0; se <= 5; se++) st().setCellParam(cellId, `SE${se}`, true);
    rebuild(ctx);
    setPointer(ctx, { clientX: 50, clientY: 50 } as unknown as PointerEvent);
    assert.equal(pickBuilderMesh(ctx), null);
  });
});

test("a row excluded only through the SE list is hidden too (spaceSideExcluded reads both)", () => {
  withSpace((ctx, cellId) => {
    st().updateCell(cellId, { params: { ...st().cells[cellId].params, SE: [2] } });
    rebuild(ctx);
    const mesh = ctx.meshById.get(cellId)!;
    assert.equal((mesh.material as THREE.Material[])[NEG_Y_MATERIAL_INDEX].visible, false);
  });
});
