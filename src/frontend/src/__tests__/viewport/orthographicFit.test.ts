// A fit in the orthographic view (issue #552): the view's half-height comes from the box as it lies on
// the view plane, with the fit margin, not from the distance a perspective fit works out. Checked on the
// shared maths (viewport/api/cameraFrame) and on the three.js viewport's fits, for a cube and for a long,
// thin model.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import * as THREE from "three";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string) => memory.get(k) ?? null,
  setItem: (k: string, v: string) => void memory.set(k, String(v)),
  removeItem: (k: string) => void memory.delete(k),
};
(globalThis as unknown as Record<string, unknown>).localStorage ??= storage;
(globalThis as unknown as Record<string, unknown>).sessionStorage ??= storage;

const { framePose, ORTHO_FIT_MARGIN, orthoFramePose, projectedHalfExtents, sphereFitDistance } = await import("@/viewport/api/cameraFrame");
const { bindOrthographic, orthoHalfHeight } = await import("@/utils/scene/orthographicProjection");
const { frameBox, orthoFitPose, zoomToAll } = await import("@/components/viewer/sceneHelpers/cameraFit");
const { center_on_bounding_box } = await import("@/utils/scene/centerViewOnSelection");
const { applyNamedView } = await import("@/components/viewer/sceneHelpers/namedViews");
const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls");
const { useModelState } = await import("@/state/modelState");

type Box6 = [number, number, number, number, number, number];

const near = (a: number, b: number, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

const CUBE: Box6 = [-1, -1, -1, 1, 1, 1];
/** 20 long in x, 0.2 across: a beam. */
const BEAM: Box6 = [-10, -0.1, -0.1, 10, 0.1, 0.1];
const corners = (b: Box6) =>
  Array.from({ length: 8 }, (_, i) => new THREE.Vector3(i & 1 ? b[3] : b[0], i & 2 ? b[4] : b[1], i & 4 ? b[5] : b[2]));

/** How far the box reaches towards the view's edges, in normalised device coordinates: 1 is the edge. */
function reach(camera: THREE.Camera, box: Box6): { x: number; y: number } {
  camera.updateMatrixWorld();
  let x = 0, y = 0;
  for (const c of corners(box)) {
    const p = c.project(camera);
    x = Math.max(x, Math.abs(p.x));
    y = Math.max(y, Math.abs(p.y));
  }
  return { x, y };
}

test("the shared orthographic fit: a cube seen face on fills the view to the margin", () => {
  // Looking along +y (the front view in a z-up world), z up on screen; a wide view.
  const pose = orthoFramePose(CUBE, [0, 1, 0], [0, 0, 1], 60, 1.5)!;
  near(pose.halfHeight, 1 * ORTHO_FIT_MARGIN);
  assert.deepEqual(pose.target, [0, 0, 0]);
  // The camera stands where the view, as tall as the perspective one there, has that half-height.
  const distance = Math.hypot(...pose.eye);
  near(orthoHalfHeight(distance, 60), pose.halfHeight);
  assert.ok(pose.eye[1] < 0, "in front of the cube, looking along +y");
});

test("the shared orthographic fit: a long, thin model is decided by its length and the view's width", () => {
  const aspect = 1.5;
  const pose = orthoFramePose(BEAM, [0, 1, 0], [0, 0, 1], 60, aspect)!;
  // 10 to each side across a view 1.5 times as wide as it is tall.
  near(pose.halfHeight, (10 / aspect) * ORTHO_FIT_MARGIN);
  // Seen end on, its cross-section decides.
  const endOn = orthoFramePose(BEAM, [1, 0, 0], [0, 0, 1], 60, aspect)!.halfHeight;
  near(endOn, 0.1 * ORTHO_FIT_MARGIN);
  // A perspective fit's distance gives a view far taller than the beam needs end on: the sphere
  // round the beam is 20 across whichever way it is seen.
  const loose = orthoHalfHeight(sphereFitDistance(Math.hypot(10, 0.1, 0.1), 60, aspect), 60);
  assert.ok(loose > 50 * endOn, `the perspective-derived half-height (${loose}) is loose`);
});

test("projected extents: a view along the hint falls back to the widest extent across the view", () => {
  const along = projectedHalfExtents(BEAM, [0, 0, -1], [0, 0, 1]);
  near(along.x, Math.hypot(10, 0.1));
  near(along.y, along.x);
  const front = projectedHalfExtents(BEAM, [0, 1, 0], [0, 0, 1]);
  near(front.x, 10);
  near(front.y, 0.1);
  assert.equal(orthoFramePose([1, 1, 1, 0, 0, 0], [0, 1, 0], [0, 0, 1], 60, 1), null, "an empty box");
  assert.equal(orthoFramePose([1, 1, 1, 1, 1, 1], [0, 1, 0], [0, 0, 1], 60, 1), null, "a box of no size");
});

// --- The three.js viewport -------------------------------------------------------------------------

const fakeElement = () => {
  const el = {
    style: {} as Record<string, string>,
    addEventListener: () => {},
    removeEventListener: () => {},
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    getRootNode: () => el,
    ownerDocument: null as unknown,
    clientWidth: 1200,
    clientHeight: 800,
  };
  el.ownerDocument = el;
  return el as unknown as HTMLElement;
};

/** The viewport's camera, drawing orthographically, looking along +y from `distance` away. */
function rig(orthographic = true, distance = 30) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1.5, 0.1, 1000);
  camera.up.set(0, 0, 1);
  camera.position.set(0, -distance, 0);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  const controls = new OrbitControls(camera, fakeElement());
  const release = bindOrthographic(scene, camera, () => controls.target, () => orthographic);
  const add = (box: Box6) => {
    const size = new THREE.Vector3(box[3] - box[0], box[4] - box[1], box[5] - box[2]);
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), new THREE.MeshBasicMaterial());
    mesh.position.set((box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2);
    scene.add(mesh);
    scene.updateMatrixWorld(true);
  };
  return { scene, camera, controls, add, release };
}

const box3 = (b: Box6) => new THREE.Box3(new THREE.Vector3(b[0], b[1], b[2]), new THREE.Vector3(b[3], b[4], b[5]));

afterEach(() => useModelState.setState({ zIsUp: true }));

for (const [name, box, decides] of [
  ["a cube", CUBE, "y"],
  ["a long, thin model", BEAM, "x"],
] as const) {
  test(`fit all in the orthographic view frames ${name} to the margin`, () => {
    const { scene, camera, controls, add, release } = rig();
    add(box);
    zoomToAll(scene, camera, controls);
    const r = reach(camera, box);
    // The way that decides reaches the margin; the other stays inside it.
    near(r[decides], 1 / ORTHO_FIT_MARGIN, 1e-4);
    assert.ok(r.x <= 1 / ORTHO_FIT_MARGIN + 1e-4 && r.y <= 1 / ORTHO_FIT_MARGIN + 1e-4);
    // The view direction is kept: still looking along +y.
    const dir = camera.getWorldDirection(new THREE.Vector3());
    near(dir.y, 1, 1e-6);
    release();
  });

  test(`fit to selection in the orthographic view frames ${name} to the margin`, () => {
    const { camera, controls, release } = rig();
    center_on_bounding_box(box3(box), camera, 1, controls);
    near(reach(camera, box)[decides], 1 / ORTHO_FIT_MARGIN, 1e-4);
    release();
  });
}

test("a fit in perspective is unchanged: the box's sphere at the perspective distance", () => {
  const { scene, camera, controls, add, release } = rig(false);
  add(BEAM);
  zoomToAll(scene, camera, controls);
  const want = framePose(BEAM, [0, 1, 0], 60, 1.5)!;
  near(camera.position.distanceTo(controls.target), Math.hypot(...want.eye), 1e-6);
  assert.equal(orthoFitPose(camera, BEAM), null, "no orthographic fit in perspective");
  release();
});

test("the long, thin model fills an orthographic fit much more than the perspective-derived one did", () => {
  const ortho = rig();
  ortho.add(BEAM);
  zoomToAll(ortho.scene, ortho.camera, ortho.controls);
  const tight = reach(ortho.camera, BEAM).x;
  // The old fit: the perspective distance, and the orthographic half-height derived from it.
  const old = rig();
  const pose = framePose(BEAM, [0, 1, 0], 60, 1.5)!;
  old.camera.position.set(...pose.eye);
  old.controls.target.set(...pose.target);
  old.camera.lookAt(0, 0, 0);
  old.camera.updateProjectionMatrix();
  const loose = reach(old.camera, BEAM).x;
  assert.ok(tight > loose * 1.3, `the orthographic fit (${tight}) fills more than the old one (${loose})`);
  ortho.release();
  old.release();
});

test("a framed box and a named view in the orthographic view are sized from the box's outline", () => {
  const { scene, camera, controls, add, release } = rig();
  frameBox(box3(BEAM), camera, controls);
  // frameBox leaves its usual extra room (1.6) on top of the margin.
  near(reach(camera, BEAM).x, 1 / (ORTHO_FIT_MARGIN * 1.6), 1e-4);
  add(BEAM);
  // The right view looks along -x: the beam end on, its cross-section decides.
  applyNamedView("right", scene, camera, controls, true);
  near(reach(camera, BEAM).y, 1 / ORTHO_FIT_MARGIN, 1e-3);
  release();
});
