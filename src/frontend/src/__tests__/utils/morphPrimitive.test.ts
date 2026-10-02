import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

import {findMorphPrimitive} from "@/utils/scene/fea/morphPrimitive";

// The paradoc embed starts a mode's animation session on the primitive that
// carries the mode-shape morph. A beam model has no faces -- its morph sits on
// the node Points (or a LineSegments) -- and a Mesh-only lookup left every beam
// case undeformed and without the simulation controls.

test("a beam model's node Points carries the morph", () => {
    const scene = new THREE.Scene();
    const nodes = morphed(new THREE.Points(geometry(), new THREE.PointsMaterial()));
    scene.add(nodes);
    assert.equal(findMorphPrimitive(scene), nodes);
});

function morphed<T extends THREE.Object3D & {geometry: THREE.BufferGeometry}>(obj: T): T {
    const n = obj.geometry.getAttribute("position").count;
    obj.geometry.morphAttributes.position = [new THREE.Float32BufferAttribute(new Float32Array(n * 3), 3)];
    return obj;
}

function geometry(): THREE.BufferGeometry {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0], 3));
    return g;
}

test("a line-only model's LineSegments carries the morph", () => {
    const scene = new THREE.Scene();
    const beam = morphed(new THREE.LineSegments(geometry(), new THREE.LineBasicMaterial()));
    scene.add(beam);
    assert.equal(findMorphPrimitive(scene), beam);
});

test("a shell / solid model's Mesh carries the morph", () => {
    const scene = new THREE.Scene();
    const surface = morphed(new THREE.Mesh(geometry(), new THREE.MeshBasicMaterial()));
    scene.add(surface);
    assert.equal(findMorphPrimitive(scene), surface);
});

test("the primitive wins over its morphed wireframe-edge child", () => {
    const scene = new THREE.Scene();
    const beam = morphed(new THREE.LineSegments(geometry(), new THREE.LineBasicMaterial()));
    const edges = morphed(new THREE.LineSegments(geometry(), new THREE.LineBasicMaterial()));
    beam.add(edges);
    scene.add(beam);
    assert.equal(findMorphPrimitive(scene), beam);
});

test("nothing morphed, nothing found", () => {
    const scene = new THREE.Scene();
    scene.add(new THREE.Mesh(geometry(), new THREE.MeshBasicMaterial()));
    scene.add(new THREE.LineSegments(geometry(), new THREE.LineBasicMaterial()));
    assert.equal(findMorphPrimitive(scene), null);
});
