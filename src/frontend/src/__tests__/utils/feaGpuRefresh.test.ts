import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import {
  ensureVertexNormals,
  refreshMorphTexture,
  resetBasePositions,
  writeColorAttribute,
  writeMorphTarget,
} from "../../utils/scene/fea/gpuRefresh";
import { setSourceMorph } from "../../utils/scene/fea/sourceMorph";
import { upcomingSteps } from "../../utils/scene/fea/streaming/prefetch";

function triangle(): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
  g.setIndex([0, 1, 2]);
  return g;
}

/** What three's WebGLGeometries does on "dispose": delete every attribute buffer
 *  it finds on the geometry. Records them so a test can see which would go. */
function watchDisposedBuffers(g: THREE.BufferGeometry): unknown[] {
  const removed: unknown[] = [];
  g.addEventListener("dispose", (e) => {
    const geom = (e as unknown as { target: THREE.BufferGeometry }).target;
    if (geom.index) removed.push(geom.index);
    for (const name in geom.attributes) removed.push(geom.attributes[name]);
  });
  return removed;
}

test("refreshMorphTexture fires dispose without exposing any attribute buffer", () => {
  const g = triangle();
  const position = g.getAttribute("position");
  const index = g.getIndex();
  const removed = watchDisposedBuffers(g);
  let fired = 0;
  g.addEventListener("dispose", () => fired++);

  refreshMorphTexture(g);

  assert.equal(fired, 1, "the morph texture's own listener still hears it");
  assert.deepEqual(removed, [], "no attribute buffer is deleted");
  assert.equal(g.getAttribute("position"), position, "attributes are back afterwards");
  assert.equal(g.getIndex(), index);
});

test("writeMorphTarget writes in place and bumps the version", () => {
  const g = triangle();
  writeMorphTarget(g, new Float32Array([1, 1, 1, 2, 2, 2, 3, 3, 3]), null);
  const list = g.morphAttributes.position;
  const attr = list[0] as THREE.BufferAttribute;
  const array = attr.array;
  const version = attr.version;

  writeMorphTarget(g, new Float32Array([4, 4, 4, 5, 5, 5, 6, 6, 6]), null);

  assert.equal(g.morphAttributes.position, list, "the morph array a wireframe shares is kept");
  assert.equal(g.morphAttributes.position[0], attr, "same attribute");
  assert.equal(attr.array, array, "same backing array");
  assert.ok(attr.version > version, "the WebGPU mirror sees a new version");
  assert.deepEqual(Array.from(attr.array), [4, 4, 4, 5, 5, 5, 6, 6, 6]);
  assert.equal(g.morphTargetsRelative, true);
});

test("writeMorphTarget expands through an element-local map and replaces a misfit", () => {
  const g = triangle();
  writeMorphTarget(g, new Float32Array([1, 1, 1, 2, 2, 2, 3, 3, 3]), null);
  const before = g.morphAttributes.position[0];
  // Four render vertices over three source vertices: the geometry was expanded.
  writeMorphTarget(g, new Float32Array([1, 1, 1, 2, 2, 2, 3, 3, 3]), new Uint32Array([2, 0, 0, 1]));
  const after = g.morphAttributes.position[0] as THREE.BufferAttribute;
  assert.notEqual(after, before);
  assert.deepEqual(Array.from(after.array), [3, 3, 3, 1, 1, 1, 1, 1, 1, 2, 2, 2]);
});

test("writeColorAttribute reuses the colour buffer", () => {
  const g = triangle();
  writeColorAttribute(g, new Float32Array(9).fill(0.5), null);
  const color = g.getAttribute("color");
  writeColorAttribute(g, new Float32Array(9).fill(0.25), null);
  assert.equal(g.getAttribute("color"), color);
  assert.equal(color.getX(2), 0.25);
});

test("normals are computed once per position attribute, not once per step", () => {
  const g = triangle();
  let computed = 0;
  const original = g.computeVertexNormals.bind(g);
  g.computeVertexNormals = () => {
    computed++;
    original();
  };
  ensureVertexNormals(g);
  ensureVertexNormals(g);
  assert.equal(computed, 1);
  // An element-local expansion swaps the position attribute: compute again.
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
  ensureVertexNormals(g);
  assert.equal(computed, 2);
});

test("resetBasePositions skips the copy and the upload when the base is already there", () => {
  const g = triangle();
  const base = new Float32Array([0, 0, 0, 2, 0, 0, 0, 2, 0]);
  const pos = g.getAttribute("position") as THREE.BufferAttribute;
  resetBasePositions(g, base, null);
  const version = pos.version;
  assert.equal(pos.getX(1), 2);
  resetBasePositions(g, base, null);
  assert.equal(pos.version, version, "no second upload");
});

test("setSourceMorph keeps the wireframe's morph attribute across steps", () => {
  const mesh = new THREE.Mesh(triangle());
  const edges = new THREE.LineSegments(triangle(), new THREE.LineBasicMaterial());
  edges.name = "fea-element-edges";
  mesh.add(edges);
  setSourceMorph(mesh, "fea-element-edges", new Float32Array(9).fill(1));
  const attr = edges.geometry.morphAttributes.position[0];
  const removed = watchDisposedBuffers(edges.geometry);
  setSourceMorph(mesh, "fea-element-edges", new Float32Array(9).fill(2));
  assert.equal(edges.geometry.morphAttributes.position[0], attr);
  assert.equal((attr as THREE.BufferAttribute).getX(0), 2);
  assert.deepEqual(removed, [], "the shared position buffer survives a step");
});

test("setSourceMorph never writes into a morph it shares with the parent", () => {
  const mesh = new THREE.Mesh(triangle());
  writeMorphTarget(mesh.geometry, new Float32Array(9).fill(7), null);
  const edges = new THREE.LineSegments(triangle(), new THREE.LineBasicMaterial());
  edges.name = "fea-element-edges";
  edges.geometry.morphAttributes.position = mesh.geometry.morphAttributes.position;
  mesh.add(edges);
  setSourceMorph(mesh, "fea-element-edges", new Float32Array(9).fill(1));
  assert.equal((mesh.geometry.morphAttributes.position[0] as THREE.BufferAttribute).getX(0), 7);
  assert.equal((edges.geometry.morphAttributes.position[0] as THREE.BufferAttribute).getX(0), 1);
});

test("play reads ahead the next frames, wrapping at the end", () => {
  assert.deepEqual(upcomingSteps(0, 70, 4), [1, 2, 3, 4]);
  assert.deepEqual(upcomingSteps(68, 70, 4), [69, 0, 1, 2]);
  assert.deepEqual(upcomingSteps(0, 3, 4), [1, 2]);
  assert.deepEqual(upcomingSteps(0, 1, 4), []);
});
