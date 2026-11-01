import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import { FEA_STEP_CACHE } from "../../services/fea/stepCache";
import type { FrameDisplacementBackend } from "../../utils/scene/fea/playbackFrames";
import { FrameRecorder, frameBackendFor } from "../../utils/scene/fea/playbackFrames";
import { patchVertexShader, ThreeFrameBackend } from "../../utils/scene/fea/playbackFrames/threeBackend";
import { WebGpuFrameBackend } from "../../utils/scene/fea/playbackFrames/webgpuBackend";
import { sourceDisplacementOf } from "../../utils/scene/fea/streaming/frameRecording";

class FakeBackend implements FrameDisplacementBackend {
  readonly kind = "three" as const;
  stored = new Map<number, Float32Array>();
  presented: number[] = [];
  released = 0;
  disposed = false;
  displacementBytes(n: number) { return n * 12; }
  allocate() {}
  store(slot: number, d: Float32Array) { this.stored.set(slot, Float32Array.from(d)); }
  present(slot: number) { this.presented.push(slot); }
  release() { this.released++; }
  dispose() { this.disposed = true; }
}

function triangleMesh(): THREE.Mesh {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
  g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(9), 3));
  g.setIndex([0, 1, 2]);
  const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial());
  m.morphTargetInfluences = [1];
  return m;
}

function paint(mesh: THREE.Mesh, value: number): void {
  const c = mesh.geometry.getAttribute("color");
  (c.array as Float32Array).fill(value);
}

test("the recorder keeps a painted frame and shows it again through its adapter", () => {
  const backend = new FakeBackend();
  const mesh = triangleMesh();
  const rec = new FrameRecorder("k", backend, 3, 3, 0, 10, 256, 1e9);
  try {
    paint(mesh, 0.2);
    assert.equal(rec.record(4, mesh, new Float32Array(9).fill(1), null), true);
    paint(mesh, 0.8);
    assert.equal(rec.record(5, mesh, new Float32Array(9).fill(2), null), true);
    assert.equal(rec.record(5, mesh, new Float32Array(9), null), false, "once per step");
    assert.deepEqual([...backend.stored.keys()], [0, 1]);

    assert.equal(rec.show(4, mesh, null, 3), true);
    const c = mesh.geometry.getAttribute("color");
    assert.ok(Math.abs(c.getX(0) - 0.2) < 1 / 255);
    assert.equal(mesh.morphTargetInfluences![0], 3, "the influence the ordinary paint would install");
    assert.deepEqual(backend.presented, [0]);
    assert.equal(rec.show(7, mesh, null, 1), false, "not recorded");
  } finally {
    rec.dispose();
  }
  assert.ok(backend.disposed);
});

test("the recorder's capacity comes out of the step-cache budget and goes back after", () => {
  const before = FEA_STEP_CACHE.budgetBytes;
  const perFrame = 3 * 12 + 3 * 3; // displacement + colours, no beams
  const rec = new FrameRecorder("k", new FakeBackend(), 3, 3, 0, 100, 256, perFrame * 8); // half the budget: 4 frames
  assert.equal(rec.capacity, 4);
  assert.equal(FEA_STEP_CACHE.budgetBytes, before - 4 * perFrame);
  const mesh = triangleMesh();
  for (let s = 0; s < 6; s++) rec.record(s, mesh, new Float32Array(9), null);
  assert.equal(rec.size, 4, "frames past the capacity paint the ordinary way");
  rec.dispose();
  assert.equal(FEA_STEP_CACHE.budgetBytes, before);
});

test("a mesh that changed under the recording is not shown from it", () => {
  const rec = new FrameRecorder("k", new FakeBackend(), 3, 3, 0, 10, 256, 1e9);
  const mesh = triangleMesh();
  rec.record(0, mesh, new Float32Array(9), null);
  mesh.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(12), 3));
  assert.equal(rec.show(0, mesh, null, 1), false);
  rec.dispose();
});

test("each renderer gets its own adapter", () => {
  assert.ok(frameBackendFor("three").backend instanceof ThreeFrameBackend);
  assert.ok(frameBackendFor("adacpp-webgpu").backend instanceof WebGpuFrameBackend);
  assert.equal(frameBackendFor("three").maxFrames, 256);
});

test("the WebGPU adapter writes the frame into the morph in place for the scene mirror", () => {
  const mesh = triangleMesh();
  mesh.geometry.morphAttributes.position = [new THREE.BufferAttribute(new Float32Array(9), 3)];
  const morph = mesh.geometry.morphAttributes.position[0] as THREE.BufferAttribute;
  const version = morph.version;
  const be = new WebGpuFrameBackend();
  be.allocate(3, 2);
  be.store(1, new Float32Array([1, 2, 3, 4, 5, 6, 7, 8, 9]));
  be.present(1, mesh, null);
  assert.equal(mesh.geometry.morphAttributes.position[0], morph, "same attribute: the mirror tracks identity");
  assert.ok(morph.version > version, "and version");
  assert.deepEqual(Array.from(morph.array), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
});

test("the shader patch reads the frame after three's own morph, and only where there is one", () => {
  const src = "#include <common>\nvoid main(){\n#include <morphtarget_vertex>\n}";
  const mapped = patchVertexShader(src, true);
  assert.match(mapped, /attribute float feaSrc;/);
  assert.match(mapped, /#include <morphtarget_vertex>\n[\s\S]*texelFetch\( feaDispTex/);
  assert.doesNotMatch(patchVertexShader(src, false), /feaSrc/);
  assert.match(patchVertexShader(src, false), /gl_VertexID/);
  assert.equal(patchVertexShader("void main(){}", true), "void main(){}");
});

test("the recorded displacement is the warp field's translation slots per source vertex", () => {
  const field = { components: ["ALL", "X", "Y", "Z"], name_canonical: "U" } as never;
  const out = sourceDisplacementOf({ field, stepValues: new Float32Array([9, 1, 2, 3, 9, 4, 5, 6]) }, 2);
  assert.deepEqual(Array.from(out), [1, 2, 3, 4, 5, 6]);
  assert.deepEqual(Array.from(sourceDisplacementOf(null, 2)), [0, 0, 0, 0, 0, 0]);
});
