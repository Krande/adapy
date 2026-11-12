// A procedural model opened into the builder is framed once: the builder's group is
// left out of "fit all", so without this the camera stays wherever it was.

import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

const { frameOpenedModel } = await import("@/components/viewer/cellbuilder/frameOpened");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;

test("a newly opened model is framed once, again after it is closed, never while empty", () => {
  const boxes: number[][][] = [];
  const withdraw = setActiveViewport({
    fitBox: (min: number[], max: number[]) => (boxes.push([min, max]), true),
  } as unknown as ViewportApi);
  try {
    const cells = new THREE.Group();
    assert.equal(frameOpenedModel(cells, "m1", null), null, "nothing drawn yet: wait");
    assert.equal(boxes.length, 0);

    const box = new THREE.Mesh(new THREE.BoxGeometry(2, 4, 6));
    box.position.set(10, 0, 0);
    cells.add(box);
    let framed = frameOpenedModel(cells, "m1", null);
    assert.equal(framed, "m1");
    assert.deepEqual(boxes, [[[9, -2, -3], [11, 2, 3]]]);

    framed = frameOpenedModel(cells, "m1", framed); // an edit, a commit: not again
    assert.equal(boxes.length, 1);
    framed = frameOpenedModel(cells, null, framed); // closed
    assert.equal(framed, null);
    assert.equal(frameOpenedModel(cells, "m1", framed), "m1"); // reopened: framed again
    assert.equal(boxes.length, 2);
  } finally {
    withdraw();
  }
});

test("the framer keeps trying on the next frames until the viewport can frame", async () => {
  const { openedModelFramer } = await import("@/components/viewer/cellbuilder/frameOpened");
  let ready = false;
  let fits = 0;
  const withdraw = setActiveViewport({
    fitBox: () => (ready ? (fits++, true) : false),
  } as unknown as ViewportApi);
  const queued: (() => void)[] = [];
  try {
    const cells = new THREE.Group();
    cells.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)));
    const framer = openedModelFramer(cells, () => "m1", (cb) => queued.push(cb), () => {});
    framer.check();
    assert.equal(fits, 0);
    assert.equal(queued.length, 1, "not ready: asks again next frame");
    ready = true;
    queued.shift()!();
    assert.equal(fits, 1);
    assert.equal(queued.length, 0, "framed: stops");
    framer.check(); // a later edit
    assert.equal(fits, 1);
  } finally {
    withdraw();
  }
});
