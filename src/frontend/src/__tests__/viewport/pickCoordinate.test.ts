// The clicked coordinate of the Selected Object panel, from a click in the three.js-free viewport.
import assert from "node:assert/strict";
import { test } from "node:test";

import { modelPoint } from "@/plugins/scenePicks";
import { pickCoordinate } from "@/viewport/api/viewportApi";

test("a click on an object names its point; empty space and a pick without a point name none", () => {
  const ref = { model: "m1", id: "r1" };
  assert.deepEqual(pickCoordinate({ ref, point: [1, 2, 3] }), { x: 1, y: 2, z: 3 });
  assert.equal(pickCoordinate({ ref, point: null }), null);
  assert.equal(pickCoordinate({ ref }), null);
  assert.equal(pickCoordinate({ ref: null, point: [1, 2, 3] }), null);
});

test("the point shown is in the model's own coordinates, whatever the viewport moved the model by", () => {
  // The viewport drew the model recentred by (-100, -50, 0) and its pick hit (2, 3, 4) in its world.
  const point = modelPoint([2, 3, 4], [-100, -50, 0]);
  assert.deepEqual(pickCoordinate({ ref: { model: "m1", id: "r1" }, point }), { x: 102, y: 53, z: 4 });
});
