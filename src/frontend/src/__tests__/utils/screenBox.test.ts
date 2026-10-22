// A rectangle drawn on the screen: window (dragged rightwards) takes what is wholly inside,
// crossing (leftwards) what it touches. The identity matrix stands in for the camera, so world
// x and y are the screen's NDC.
import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {CustomBatchedMesh} from "../../utils/mesh_select/CustomBatchedMesh";
import {ndcRect, rangesInScreenRect, screenBoxMode, triangleMeetsRect} from "../../utils/bboxSelection/screenBox";

const unit = {x0: 0, y0: 0, x1: 1, y1: 1};
const identity = new THREE.Matrix4();

/** A inside the unit square, B reaching out past its right edge, C far away, D a diagonal whose
 *  bounding box overlaps the square's corner while the triangle itself stays clear of it. */
function elements(): CustomBatchedMesh {
    const tris = [
        [[0.2, 0.2, 0], [0.8, 0.2, 0], [0.5, 0.8, 0]],
        [[0.5, 0.5, 0], [3, 0.5, 0], [3, 0.9, 0]],
        [[10, 10, 0], [11, 10, 0], [10, 11, 0]],
        [[1.8, 0.8, 0], [0.8, 1.8, 0], [1.8, 1.8, 0]],
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
        ["D", [9, 3]],
    ]);
    return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "m", true, null);
}

test("dragged rightwards is a window, leftwards a crossing", () => {
    assert.equal(screenBoxMode(10, 50), "window");
    assert.equal(screenBoxMode(50, 10), "crossing");
});

test("a window takes only what is wholly inside", () => {
    assert.deepEqual(rangesInScreenRect(elements(), identity, unit, "window"), ["A"]);
});

test("a crossing takes what it touches, not what only its bounding box reaches", () => {
    assert.deepEqual(rangesInScreenRect(elements(), identity, unit, "crossing").sort(), ["A", "B"]);
});

test("hidden elements are not taken", () => {
    assert.deepEqual(rangesInScreenRect(elements(), identity, unit, "crossing", new Set(["A"])), ["B"]);
});

test("geometry behind the camera is never inside", () => {
    const behind = new THREE.Matrix4().set(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1);
    assert.deepEqual(rangesInScreenRect(elements(), behind, unit, "crossing"), []);
});

test("a triangle overlapping only the rectangle's bounding corner does not meet it", () => {
    assert.ok(!triangleMeetsRect([1.8, 0.8, true], [0.8, 1.8, true], [1.8, 1.8, true], unit));
    assert.ok(triangleMeetsRect([0.5, 0.5, true], [3, 0.5, true], [3, 0.9, true], unit));
});

test("the rectangle between two client points is in NDC, whichever way it was dragged", () => {
    const rect = {left: 0, top: 0, width: 200, height: 100} as DOMRect;
    assert.deepEqual(ndcRect({x: 150, y: 75}, {x: 50, y: 25}, rect), {x0: -0.5, x1: 0.5, y0: -0.5, y1: 0.5});
});
