// Picking on the three.js viewport's batched mesh: a ray hits the deformed shape the user sees
// (morph targets applied on the CPU as the GPU applies them), and never a hidden range.
import assert from "node:assert/strict";
import {test} from "node:test";
import * as THREE from "three";

import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {applyMorphTargets} from "@/utils/mesh_select/customBatchedMesh/morph";

/** Two unit triangles side by side in z = 0, ranges `r0` (x 0..1) and `r1` (x 1..2). */
function mesh(): CustomBatchedMesh {
    const pos = new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 2, 0, 0, 2, 1, 0]);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setIndex(new THREE.BufferAttribute(Uint32Array.from({length: 6}, (_, i) => i), 1));
    const ranges = new Map<string, [number, number]>([["r0", [0, 3]], ["r1", [3, 3]]]);
    const m = new CustomBatchedMesh(g, new THREE.MeshBasicMaterial({side: THREE.DoubleSide}), ranges, "m", true, null);
    m.updateMatrixWorld(true);
    return m;
}

/** What a ray straight down -z through (x, y) hits: the face indices, nearest first. */
function hits(m: THREE.Mesh, x: number, y: number): number[] {
    const raycaster = new THREE.Raycaster(new THREE.Vector3(x, y, 5), new THREE.Vector3(0, 0, -1));
    const out: THREE.Intersection[] = [];
    m.raycast(raycaster, out);
    return out.sort((a, b) => a.distance - b.distance).map((h) => h.faceIndex ?? -1);
}

test("a ray hits the range under it, at the right distance", () => {
    const m = mesh();
    assert.deepEqual(hits(m, 0.75, 0.25), [0]);
    assert.deepEqual(hits(m, 1.75, 0.25), [1]);
    assert.deepEqual(hits(m, 5, 5), []);
    const out: THREE.Intersection[] = [];
    m.raycast(new THREE.Raycaster(new THREE.Vector3(0.75, 0.25, 5), new THREE.Vector3(0, 0, -1)), out);
    assert.equal(out[0].distance, 5);
    assert.deepEqual(out[0].point.toArray(), [0.75, 0.25, 0]);
});

test("a hidden range is never hit, and is again once shown", () => {
    const m = mesh();
    m.hideBatchDrawRange(["r0"]);
    assert.deepEqual(hits(m, 0.75, 0.25), []);
    assert.deepEqual(hits(m, 1.75, 0.25), [1]);
    m.unhideBatchDrawRange(["r0"]);
    assert.deepEqual(hits(m, 0.75, 0.25), [0]);
});

test("a ray hits the morphed shape, not the rest shape", () => {
    const m = mesh();
    // One relative target moving everything 10 along x.
    const offsets = new Float32Array(18);
    for (let i = 0; i < 6; i++) offsets[i * 3] = 10;
    m.geometry.morphAttributes.position = [new THREE.BufferAttribute(offsets, 3)];
    m.geometry.morphTargetsRelative = true;
    m.updateMorphTargets();
    m.morphTargetInfluences![0] = 1;
    m.geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(6, 0.5, 0), 20);
    assert.deepEqual(hits(m, 0.75, 0.25), []);
    assert.deepEqual(hits(m, 10.75, 0.25), [0]);
});

test("morph targets: relative ones add, absolute ones blend towards the target", () => {
    const target = (x: number, y: number, z: number) => new THREE.BufferAttribute(new Float32Array([x, y, z]), 3);
    const v = new THREE.Vector3(1, 1, 1);
    applyMorphTargets(v, 0, [target(2, 0, 0), target(0, 4, 0)], [0.5, 0.25], true);
    assert.deepEqual(v.toArray(), [2, 2, 1]);

    const w = new THREE.Vector3(0, 0, 0);
    applyMorphTargets(w, 0, [target(10, 10, 10)], [0.5], false);
    assert.deepEqual(w.toArray(), [5, 5, 5]);

    const u = new THREE.Vector3(1, 2, 3);
    applyMorphTargets(u, 0, undefined, [1], true);
    applyMorphTargets(u, 0, [target(9, 9, 9)], [0], true);
    assert.deepEqual(u.toArray(), [1, 2, 3], "no targets, or no influence: unchanged");
});
