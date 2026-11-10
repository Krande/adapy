// The result layer on the three.js viewport: a step's colours and displacement, and the deformation
// scale, go where three.js draws from: the geometry's colour attribute, morph target 0 and the
// mesh's morph influence. The same calls as on the three.js-free viewport
// (viewport/nativeResultLayer.test.ts).
import assert from "node:assert/strict";
import {test} from "node:test";

import * as THREE from "three";

import {createThreeResultMesh, threeResultLayer} from "@/utils/scene/fea/threeResultLayer";
import {positionsDiagonal, type ResultMeshData} from "@/viewport/api/resultLayer";

const TRIANGLE: ResultMeshData = {
    positions: new Float32Array([0, 0, 0, 2, 0, 0, 0, 3, 0]),
    indices: new Uint32Array([0, 1, 2]),
    topology: "triangles",
};
const RGB = new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 0.5]);
const MOVE = new Float32Array([0, 0, 1, 0, 0, 2, 0, 0, 3]);

test("a mesh made from typed arrays is painted, deformed and scaled through its layer", () => {
    const scene = new THREE.Scene();
    const layer = createThreeResultMesh(TRIANGLE, scene);
    const mesh = layer.object as THREE.Mesh;
    assert.equal(mesh.parent, scene);
    assert.equal(layer.vertexCount, 3);
    assert.deepEqual([...(mesh.geometry.getAttribute("position").array as Float32Array)], [...TRIANGLE.positions]);
    assert.ok(mesh.geometry.getAttribute("normal"), "faces are lit");

    layer.setDisplacement(MOVE);
    layer.setColors(RGB);
    layer.setInfluence(0.25);
    assert.deepEqual([...(mesh.geometry.morphAttributes.position[0].array as Float32Array)], [...MOVE]);
    assert.equal(mesh.geometry.morphTargetsRelative, true);
    assert.deepEqual([...(mesh.geometry.getAttribute("color").array as Float32Array)], [...RGB]);
    assert.equal((mesh.material as THREE.MeshStandardMaterial).vertexColors, true);
    assert.equal(mesh.morphTargetInfluences?.[0], 0.25);
    assert.deepEqual([...(mesh.geometry.getAttribute("position").array as Float32Array)], [...TRIANGLE.positions], "the base does not move");

    layer.setColorsVisible(false);
    assert.equal((mesh.material as THREE.MeshStandardMaterial).vertexColors, false);
    layer.setVisible(false);
    assert.equal(mesh.visible, false);
    layer.remove();
    assert.equal(mesh.parent, null);
});

test("a step is written in place, so playing allocates no new buffers", () => {
    const layer = createThreeResultMesh(TRIANGLE, new THREE.Scene());
    const geometry = (layer.object as THREE.Mesh).geometry;
    layer.setDisplacement(MOVE);
    layer.setColors(RGB);
    const morph = geometry.morphAttributes.position[0].array;
    const color = geometry.getAttribute("color").array;
    layer.setDisplacement(new Float32Array(9).fill(4));
    layer.setColors(new Float32Array(9).fill(0.5));
    assert.equal(geometry.morphAttributes.position[0].array, morph);
    assert.equal(geometry.getAttribute("color").array, color);
    assert.equal(morph[0], 4);
    assert.equal(color[0], 0.5);
});

test("a mesh already in the scene has one layer, and it scales the mesh's own influence", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(TRIANGLE.positions.slice(), 3));
    const mesh = new THREE.Mesh(geometry, [new THREE.MeshStandardMaterial(), new THREE.MeshBasicMaterial()]);
    const layer = threeResultLayer(mesh, TRIANGLE.positions);
    assert.equal(threeResultLayer(mesh, TRIANGLE.positions), layer);
    // Nothing painted yet: there is no morph to scale, and none is invented.
    layer.setInfluence(3);
    assert.equal(mesh.morphTargetInfluences, undefined);
    layer.setDisplacement(MOVE);
    layer.setColors(RGB);
    layer.setInfluence(3);
    assert.equal(mesh.morphTargetInfluences?.[0], 3);
    // Every material slot draws the colours, not the array as a whole.
    for (const m of mesh.material as THREE.Material[]) assert.equal(m.vertexColors, true);
});

test("lines over the same nodes follow the same displacement", () => {
    const layer = createThreeResultMesh({positions: TRIANGLE.positions, indices: new Uint32Array([0, 1, 1, 2]), topology: "lines", color: [1, 1, 1]}, new THREE.Scene());
    const lines = layer.object as THREE.LineSegments;
    assert.ok(lines.isLineSegments);
    layer.setDisplacement(MOVE);
    layer.setInfluence(2);
    assert.deepEqual([...(lines.geometry.morphAttributes.position[0].array as Float32Array)], [...MOVE]);
    assert.equal(lines.morphTargetInfluences?.[0], 2);
});

test("the size a deformation scale is chosen against is the diagonal of the undeformed box", () => {
    assert.equal(positionsDiagonal(TRIANGLE.positions), Math.hypot(2, 3));
    assert.equal(positionsDiagonal(new Float32Array(0)), 0);
});
