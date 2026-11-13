// A mesh written without an index buffer (every three vertices a triangle) is drawn, hidden and
// selected by ranges of one all the same: it is given the index that says what it had.
import assert from "node:assert/strict";
import {test} from "node:test";

import * as THREE from "three";

import {ensureIndexed} from "@/utils/scene/ensureIndexed";

test("a mesh without an index gets one that names its vertices in order", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6 * 3), 3));
    ensureIndexed(geometry);
    assert.deepEqual(Array.from(geometry.index!.array), [0, 1, 2, 3, 4, 5]);
});

test("an index that is there is left as it is, and a mesh of no vertices is left alone", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(4 * 3), 3));
    geometry.setIndex([0, 1, 2, 0, 2, 3]);
    const index = geometry.index;
    ensureIndexed(geometry);
    assert.equal(geometry.index, index);
    const empty = new THREE.BufferGeometry();
    ensureIndexed(empty);
    assert.equal(empty.index, null);
});

test("more vertices than sixteen bits count get a wider index", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(65538 * 3), 3));
    ensureIndexed(geometry);
    assert.ok(geometry.index!.array instanceof Uint32Array);
    assert.equal(geometry.index!.array[65537], 65537);
});
