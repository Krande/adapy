// An orthographic view on the viewport's one PerspectiveCamera: as tall at the target as the
// perspective view, parallel rays from the pointer, constant-size sprites kept their size, and
// back to perspective when switched off.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

const { bindOrthographic, installOrthographicView, isOrthographic, orthoHalfHeight, sizeSpriteForView } = await import(
  "@/utils/scene/orthographicProjection"
);
const { useProjectionStore } = await import("@/state/projectionStore");

const near = (a: number, b: number, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

function rig(distance = 10) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 2, 0.1, 1000);
  camera.position.set(0, 0, distance);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  const target = new THREE.Vector3();
  let ortho = false;
  const release = bindOrthographic(scene, camera, () => target, () => ortho);
  const set = (on: boolean) => {
    ortho = on;
    camera.updateProjectionMatrix();
  };
  return { scene, camera, target, set, release };
}

test("the orthographic view is as tall at the target as the perspective one", () => {
  const { camera, set } = rig(10);
  const h = orthoHalfHeight(10, 60);
  near(h, 10 * Math.tan(Math.PI / 6));
  // Perspective: a point at the target's depth, h up, is at the top edge.
  const top = new THREE.Vector3(0, h, 0).project(camera);
  near(top.y, 1);
  set(true);
  assert.ok(isOrthographic(camera));
  near(new THREE.Vector3(0, h, 0).project(camera).y, 1);
  // ... and so is a point at the same height much nearer or farther: no perspective.
  near(new THREE.Vector3(0, h, 8).project(camera).y, 1);
  near(new THREE.Vector3(0, h, -50).project(camera).y, 1);
  near(new THREE.Vector3(2 * h, 0, 0).project(camera).x, 1, 1e-6); // aspect 2
  // Nothing between the eye and the target is cut off, nor just behind the eye.
  const behind = new THREE.Vector3(0, 0, 12).project(camera);
  assert.ok(behind.z > -1 && behind.z < 1);
});

test("zooming (moving the camera nearer) narrows the view; switching off restores perspective", () => {
  const { camera, scene, set, release } = rig(10);
  set(true);
  camera.position.set(0, 0, 5);
  camera.updateMatrixWorld();
  // The renderer asks for it each frame through the scene.
  scene.onBeforeRender(null as never, scene, camera, null as never, null as never, null as never);
  near(new THREE.Vector3(0, orthoHalfHeight(5, 60), 0).project(camera).y, 1);
  set(false);
  assert.ok(!isOrthographic(camera));
  const persp = new THREE.PerspectiveCamera(60, 2, 0.1, 1000);
  assert.deepEqual(camera.projectionMatrix.elements, persp.projectionMatrix.elements);
  release();
  assert.ok(!isOrthographic(camera));
});

test("a ray from the pointer runs along the view, wherever the pointer is", () => {
  const { camera, set } = rig(10);
  set(true);
  const ray = new THREE.Raycaster();
  ray.setFromCamera(new THREE.Vector2(0.5, -0.5), camera);
  near(ray.ray.direction.x, 0);
  near(ray.ray.direction.y, 0);
  near(ray.ray.direction.z, -1);
  // It goes through the point under the pointer at the target's depth.
  const h = orthoHalfHeight(10, 60);
  const atTarget = ray.ray.at(ray.ray.origin.z, new THREE.Vector3());
  near(atTarget.x, 0.5 * h * 2);
  near(atTarget.y, -0.5 * h);
  // And picks a box there.
  const box = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.2, 0.2));
  box.position.set(h, -0.5 * h, 0);
  box.updateMatrixWorld();
  assert.equal(ray.intersectObject(box).length, 1);
  // Perspective rays still come from the eye.
  set(false);
  ray.setFromCamera(new THREE.Vector2(0.5, -0.5), camera);
  near(ray.ray.origin.z, 10);
});

test("a constant-size sprite keeps its size on screen, and its scale when switched back", () => {
  const { camera, set } = rig(10);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ sizeAttenuation: false }));
  sprite.scale.set(0.1, 0.05, 1);
  sprite.updateMatrixWorld();
  set(true);
  sizeSpriteForView(sprite, camera);
  // scale * distance: covers scale / tan(fov/2) of the half-height, as in perspective.
  near(sprite.scale.x, 1);
  near(sprite.scale.y, 0.5);
  set(false);
  sizeSpriteForView(sprite, camera);
  near(sprite.scale.x, 0.1);
  near(sprite.scale.y, 0.05);
  // A sprite sized in the world is left alone.
  const world = new THREE.Sprite(new THREE.SpriteMaterial({ sizeAttenuation: true }));
  set(true);
  sizeSpriteForView(world, camera);
  near(world.scale.x, 1);
});

test("bound to the setting, the frame loop keeps the view in step with a dolly: no WebGLRenderer call needed", () => {
  // The adacpp WebGPU renderer draws without WebGLRenderer.render, so scene.onBeforeRender never
  // runs there: the frame loop's beforeDraw is what follows the camera's distance.
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 2, 0.1, 1000);
  camera.position.set(0, 0, 10);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  const target = new THREE.Vector3();
  let frames = 0;
  useProjectionStore.getState().setOrthographic(false);
  const view = installOrthographicView(scene, camera, () => target, () => void frames++);
  try {
    assert.ok(!isOrthographic(camera));
    useProjectionStore.getState().setOrthographic(true);
    assert.ok(isOrthographic(camera), "the setting switches the camera");
    assert.equal(frames, 2, "and asks for a frame (once when bound, once on the switch)");
    camera.position.set(0, 0, 4); // a dolly: nothing asks for the projection
    camera.updateMatrixWorld();
    view.beforeDraw();
    near(new THREE.Vector3(0, orthoHalfHeight(4, 60), 0).project(camera).y, 1);
    useProjectionStore.getState().setOrthographic(false);
    assert.ok(!isOrthographic(camera));
  } finally {
    view.release();
    useProjectionStore.getState().setOrthographic(false);
  }
  useProjectionStore.getState().setOrthographic(true);
  assert.ok(!isOrthographic(camera), "released: the camera no longer follows the setting");
  useProjectionStore.getState().setOrthographic(false);
});

test("a render that narrows the projection (the GPU mesh picker's pick matrix) keeps it; a dolly still remakes it", () => {
  const { camera, scene, set, release } = rig(10);
  set(true);
  const render = () => scene.onBeforeRender(null as never, scene, camera, null as never, null as never, null as never);
  render();
  // The picker steers the view to one pixel, then renders: the renderer asks the scene first.
  const pick = new THREE.Matrix4().makeScale(400, 300, 1).setPosition(-150, 90, 0);
  const narrowed = camera.projectionMatrix.clone().premultiply(pick);
  camera.projectionMatrix.copy(narrowed);
  render();
  assert.deepEqual(camera.projectionMatrix.elements, narrowed.elements, "the pick draws what is under the pointer, not the whole view");
  // A dolly between frames is still followed.
  camera.position.set(0, 0, 5);
  camera.updateMatrixWorld();
  render();
  near(new THREE.Vector3(0, orthoHalfHeight(5, 60), 0).project(camera).y, 1);
  set(false);
  release();
});
