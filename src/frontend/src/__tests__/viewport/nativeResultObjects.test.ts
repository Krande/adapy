// A result mesh's objects (the elements of an FEA mesh) as the three.js-free scene keeps them.
import assert from "node:assert/strict";
import {test} from "node:test";

import type {ResultMeshData} from "@/viewport/api/resultLayer";
import {growObjectBounds, indexResultSegments, resultSegments} from "@/viewport/native/resultObjects";

/** Four triangles over six nodes; objects "a" (triangle 1) and "c" (triangles 2 and 3) are drawn,
 *  "b" is not, and triangle 0 belongs to no object. */
const data: ResultMeshData = {
    positions: new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 2, 0, 5, 2, 1, 5]),
    indices: new Uint32Array([0, 1, 2, 0, 2, 3, 1, 4, 5, 1, 5, 2]),
    topology: "triangles",
    objects: {model: "m", ids: ["a", "b", "c"], segments: new Uint32Array([3, 3, 6, 6]), objectOf: new Int32Array([0, 2])},
};

test("the renderer's segments cover the index list in order, with what no object draws in between", () => {
    const {segments, objectOf} = resultSegments(data);
    assert.deepEqual([...segments], [0, 3, 3, 3, 6, 6]);
    assert.deepEqual([...objectOf], [-1, 0, 2]);
    // A tail no object draws is a segment too, and an object without triangles has none.
    const tail = resultSegments({...data, objects: {...data.objects!, segments: new Uint32Array([0, 3, 3, 0]), objectOf: new Int32Array([0, 1])}});
    assert.deepEqual([...tail.segments], [0, 3, 3, 9]);
    assert.deepEqual([...tail.objectOf], [0, -1]);
});

test("each object's box is what its triangles span, placed by the mesh's matrix", () => {
    const bounds = new Float32Array(18).map((_, i) => (i % 6 < 3 ? Infinity : -Infinity));
    const moved = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 10, 0, 0, 1];
    growObjectBounds(bounds, {...data, matrix: moved});
    assert.deepEqual([...bounds.slice(0, 6)], [10, 0, 0, 11, 1, 0]);
    assert.ok(bounds[6] > bounds[9], "an object nothing draws keeps an inverted box");
    assert.deepEqual([...bounds.slice(12, 18)], [11, 0, 0, 12, 1, 5]);
});

test("an object is drawn by the segments of every layer that has it", () => {
    const table = {count: 3, segmentOffsets: new Uint32Array(4), segments: new Uint32Array(0)};
    const layers = new Map([[7, {objectOf: new Int32Array([-1, 0, 2])}], [9, {objectOf: new Int32Array([2, 1])}]]);
    indexResultSegments(table, layers);
    assert.deepEqual([...table.segmentOffsets], [0, 1, 2, 4]);
    // (instance, segment) pairs: a by 7/1, b by 9/1, c by 7/2 and 9/0.
    assert.deepEqual([...table.segments], [7, 1, 9, 1, 7, 2, 9, 0]);
    layers.delete(9);
    indexResultSegments(table, layers);
    assert.deepEqual([...table.segmentOffsets], [0, 1, 1, 2]);
});
