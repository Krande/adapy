// The resize gizmo (S): which components it takes, the reason it gives for the rest, and
// the face resize of a rotated equipment box (the opposite face stays put).

import assert from "node:assert/strict";
import { test } from "node:test";

const THREE = await import("three");
const R = await import("@/utils/cellbuilder/cellResize");
const { applyFaceOffset } = await import("@/utils/cellbuilder/snap");

type Vec3 = [number, number, number];

test("spaces, openings and equipment resize; a loft band and a CAD-fitted equipment do not", () => {
  assert.equal(R.resizeRefusal("cell"), null);
  assert.equal(R.resizeRefusal("opening"), null);
  assert.equal(R.resizeRefusal("equipment"), null);
  assert.equal(R.resizeRefusal("equipment", { cadFitted: false }), null);
  assert.equal(R.resizeRefusal("equipment", { cadFitted: true }), R.CAD_RESIZE_REFUSAL);
  assert.equal(R.resizeRefusal("loft"), R.LOFT_RESIZE_REFUSAL);
  assert.match(R.LOFT_RESIZE_REFUSAL, /section/);
  assert.match(R.CAD_RESIZE_REFUSAL, /Use CAD models/);
  // The CAD fit only ever applies to equipment.
  assert.equal(R.resizeRefusal("cell", { cadFitted: true }), null);
});

test("an equipment box is CAD-fitted with Use CAD models on and a CAD asset on its type", () => {
  const types = [
    { slug: "pump", name: "Pump", has_cad: true },
    { slug: "tank", name: "Tank" },
  ];
  const pump = { kind: "equipment" as const, equipmentType: "PUMP" };
  assert.equal(R.equipmentCadFitted(pump, { equipmentCad: true, equipmentTypes: types }), true, "by slug, any case");
  assert.equal(R.equipmentCadFitted({ kind: "equipment", equipmentType: "Pump" }, { equipmentCad: true, equipmentTypes: types }), true, "or by name");
  assert.equal(R.equipmentCadFitted(pump, { equipmentCad: false, equipmentTypes: types }), false, "CAD models off");
  assert.equal(R.equipmentCadFitted({ kind: "equipment", equipmentType: "tank" }, { equipmentCad: true, equipmentTypes: types }), false, "no CAD asset");
  assert.equal(R.equipmentCadFitted({ kind: "equipment" }, { equipmentCad: true, equipmentTypes: types }), false, "no type");
  assert.equal(R.equipmentCadFitted({ kind: "cell", equipmentType: "pump" }, { equipmentCad: true, equipmentTypes: types }), false);
});

test("an unrotated box resizes as a face drag always has", () => {
  const box = { origin: [1, 2, 3] as Vec3, size: [4, 2, 3] as Vec3 };
  for (const rot of [undefined, null, [0, 0, 0] as Vec3]) {
    assert.deepEqual(R.resizeBoxFace(box, rot, 0, true, 1), applyFaceOffset(box, 0, true, 1));
    assert.deepEqual(R.resizeBoxFace(box, rot, 1, false, -0.5), applyFaceOffset(box, 1, false, -0.5));
  }
  assert.ok(R.boxFrameMatrix(box, [0, 0, 0]).equals(new THREE.Matrix4()), "no rotation: identity frame");
});

/** The world position of a point of `box`, given from its origin in its unrotated frame. */
const world = (box: { origin: Vec3; size: Vec3 }, rot: Vec3, p: Vec3): Vec3 => {
  const local = new THREE.Vector3(box.origin[0] + p[0], box.origin[1] + p[1], box.origin[2] + p[2]);
  const v = local.applyMatrix4(R.boxFrameMatrix(box, rot));
  return [v.x, v.y, v.z];
};
const close = (a: Vec3, b: Vec3, msg: string) =>
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 1e-6, `${msg}: ${a} vs ${b}`));

test("a rotated equipment's face moves along its own axis and the opposite face stays put", () => {
  const box = { origin: [0, 0, 0] as Vec3, size: [4, 2, 2] as Vec3 };
  const rot: Vec3 = [0, 0, 90];
  // Turned a quarter about Z: the box's +X face points along world +Y.
  close(world(box, rot, [4, 1, 1]), [2, 3, 1], "the +X face centre before");
  close(world(box, rot, [0, 1, 1]), [2, -1, 1], "the -X face centre before");
  const grown = R.resizeBoxFace(box, rot, 0, true, 1);
  assert.deepEqual(grown.size, [5, 2, 2]);
  close(world(grown, rot, [0, 1, 1]), [2, -1, 1], "the -X face stays");
  close(world(grown, rot, [5, 1, 1]), [2, 4, 1], "the +X face went a metre along world +Y");
  // The negative face, shrinking: the +X face stays.
  const shrunk = R.resizeBoxFace(box, rot, 0, false, 1);
  assert.deepEqual(shrunk.size, [3, 2, 2]);
  close(world(shrunk, rot, [3, 1, 1]), [2, 3, 1], "the +X face stays");
});

test("a box tilted about X keeps its bottom face when its top grows", () => {
  const box = { origin: [0, 0, 0] as Vec3, size: [2, 2, 2] as Vec3 };
  const rot: Vec3 = [30, 0, 15];
  const bottom = world(box, rot, [1, 1, 0]);
  const top = world(box, rot, [1, 1, 2]);
  const taller = R.resizeBoxFace(box, rot, 2, true, 0.5);
  assert.deepEqual(taller.size, [2, 2, 2.5]);
  close(world(taller, rot, [1, 1, 0]), bottom, "the bottom stays");
  // And the bottom face, lowered: the top stays.
  const lowered = R.resizeBoxFace(box, rot, 2, false, -0.5);
  assert.deepEqual(lowered.size, [2, 2, 2.5]);
  close(world(lowered, rot, [1, 1, 2.5]), top, "the top stays");
});

test("the minimum size holds for a rotated box too", () => {
  const box = { origin: [0, 0, 0] as Vec3, size: [1, 1, 1] as Vec3 };
  assert.deepEqual(R.resizeBoxFace(box, [0, 0, 45], 1, true, -5, 0.1).size, [1, 0.1, 1]);
});
