import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {CustomBatchedMesh} from "../../utils/mesh_select/CustomBatchedMesh";
import {centeredAt, faceCenter, moveFace, triangleTouchesBox, type Aabb} from "../../utils/bboxSelection/boxMath";
import {boundsOfRanges, rangesInBox} from "../../utils/bboxSelection/rangeBounds";

const unit: Aabb = {min: [0, 0, 0], max: [1, 1, 1]};

test("moving a positive face grows the box and keeps the opposite face", () => {
    assert.deepEqual(moveFace(unit, 0, 0.5, 0.01), {min: [0, 0, 0], max: [1.5, 1, 1]});
});

test("moving a negative face moves only that face", () => {
    assert.deepEqual(moveFace(unit, 3, -2, 0.01), {min: [0, -2, 0], max: [1, 1, 1]});
});

test("a face cannot pass the opposite face", () => {
    const b = moveFace(unit, 4, -5, 0.1);
    assert.equal(b.min[2], 0);
    assert.ok(Math.abs(b.max[2] - 0.1) < 1e-12);
});

test("face centres sit on the faces", () => {
    assert.deepEqual(faceCenter(unit, 1), [0, 0.5, 0.5]);
    assert.deepEqual(faceCenter(unit, 4), [0.5, 0.5, 1]);
});

test("centring keeps the size", () => {
    assert.deepEqual(centeredAt(unit, [10, 0, 0]), {min: [9.5, -0.5, -0.5], max: [10.5, 0.5, 0.5]});
});

test("a triangle crossing the box touches it", () => {
    assert.ok(triangleTouchesBox([-1, 0.5, 0.5], [2, 0.5, 0.5], [0.5, 2, 0.5], unit));
});

test("a diagonal triangle whose AABB overlaps a box corner but misses the box does not touch it", () => {
    // AABB [1.2..3]^2 overlaps a box [0..1.5]^2 at its corner; the triangle stays beyond x + y = 3.
    const box: Aabb = {min: [0, 0, 0], max: [1.5, 1.5, 1]};
    assert.ok(!triangleTouchesBox([3, 1.2, 0.5], [1.2, 3, 0.5], [3, 3, 0.5], box));
});

/** A mesh of three elements: A inside the unit box, B straddling its +X face, C far away. */
function threeElements(): CustomBatchedMesh {
    const tris = [
        [[0.2, 0.2, 0.5], [0.8, 0.2, 0.5], [0.5, 0.8, 0.5]],
        [[0.5, 0.5, 0.5], [3, 0.5, 0.5], [3, 0.9, 0.5]],
        [[10, 10, 10], [11, 10, 10], [10, 11, 10]],
    ];
    const pos: number[] = [];
    for (const t of tris) for (const v of t) pos.push(...v);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
    const ranges = new Map<string, [number, number]>([
        ["A", [0, 3]],
        ["B", [3, 3]],
        ["C", [6, 3]],
    ]);
    return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "m", true, null);
}

test("partially within takes what the box touches", () => {
    assert.deepEqual(rangesInBox(threeElements(), unit, "partial").sort(), ["A", "B"]);
});

test("fully within takes only what is entirely inside", () => {
    assert.deepEqual(rangesInBox(threeElements(), unit, "full"), ["A"]);
});

test("hidden elements are not taken", () => {
    assert.deepEqual(rangesInBox(threeElements(), unit, "partial", new Set(["A"])), ["B"]);
});

test("bounds follow the mesh's world transform", () => {
    const m = threeElements();
    m.position.set(100, 0, 0);
    const b = boundsOfRanges(m, ["A"]);
    assert.ok(b);
    assert.ok(Math.abs(b.min[0] - 100.2) < 1e-4 && Math.abs(b.max[0] - 100.8) < 1e-4);
    assert.deepEqual(rangesInBox(m, unit, "partial"), []);
});
