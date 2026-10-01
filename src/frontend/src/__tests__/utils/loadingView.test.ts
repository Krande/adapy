/**
 * A project's loading view: the framing math, its storage (per user, by scope and project, in the
 * project's own coordinates), and the bulk-load session that saves one when a load finishes.
 */

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import * as THREE from "three";

import { useModelState } from "../../state/modelState";
import { getViewerRuntime } from "../../state/viewerRuntime";
import {
  beginBulkLoad,
  bulkLoadActive,
  clearLoadingView,
  endBulkLoad,
  fitView,
  noteBulkModel,
  readLoadingView,
  writeLoadingView,
  type LoadingView,
} from "../../utils/scene/loadingView";

/** A Storage stand-in: node has no localStorage. */
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

const runtime = () => getViewerRuntime() as unknown as { camera: { current: THREE.PerspectiveCamera | null } };

beforeEach(() => {
  (globalThis as { localStorage?: Storage }).localStorage = memoryStorage();
  useModelState.setState({ translation: null });
});

afterEach(() => {
  delete (globalThis as { localStorage?: Storage }).localStorage;
  runtime().camera.current = null;
});

test("fitView stands back along the view direction until the box's sphere fits", () => {
  const box = new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1));
  const fit = fitView(box, new THREE.Vector3(0, 0, -1), 90, 1)!;
  assert.ok(fit.target.equals(new THREE.Vector3(0, 0, 0)));
  // radius sqrt(3), half-angle 45 deg: distance sqrt(3), behind the target along -direction.
  assert.ok(Math.abs(fit.position.z - Math.sqrt(3)) < 1e-9);
  assert.equal(fitView(new THREE.Box3(), new THREE.Vector3(0, 0, -1), 60, 1), null, "nothing to frame");
});

test("a loading view is kept per scope and project, and can be forgotten", () => {
  const view: LoadingView = { v: 1, translation: [1, 2, 3], position: [0, -10, 5], target: [0, 0, 0], up: [0, 0, 1] };
  writeLoadingView("user:me", "external:p/asp", view);
  assert.deepEqual(readLoadingView("user:me", "external:p/asp"), view);
  assert.equal(readLoadingView("user:me", "external:p/sde"), null, "another project");
  assert.equal(readLoadingView("project:1", "external:p/asp"), null, "another scope");
  clearLoadingView("user:me", "external:p/asp");
  assert.equal(readLoadingView("user:me", "external:p/asp"), null);
});

test("a malformed stored view reads as none", () => {
  globalThis.localStorage.setItem("ada.loadingView:user:me|x", JSON.stringify({ v: 1, position: [0, 0] }));
  assert.equal(readLoadingView("user:me", "x"), null);
  globalThis.localStorage.setItem("ada.loadingView:user:me|y", "{not json");
  assert.equal(readLoadingView("user:me", "y"), null);
});

test("a finished bulk load saves the view framing everything it loaded, in the project's coordinates", () => {
  const camera = new THREE.PerspectiveCamera(90, 1);
  camera.position.set(0, 0, 50);
  camera.lookAt(0, 0, 0);
  runtime().camera.current = camera;
  useModelState.setState({ translation: new THREE.Vector3(100, 0, 0) });

  const session = beginBulkLoad("user:me", "assets:asp");
  assert.equal(bulkLoadActive(), true);
  noteBulkModel(new THREE.Box3(new THREE.Vector3(99, -1, -1), new THREE.Vector3(101, 1, 1)));
  noteBulkModel(new THREE.Box3(new THREE.Vector3(103, -1, -1), new THREE.Vector3(105, 1, 1)));
  endBulkLoad(session);
  assert.equal(bulkLoadActive(), false);

  const saved = readLoadingView("user:me", "assets:asp")!;
  assert.deepEqual(saved.translation, [100, 0, 0]);
  // The union's centre (102, 0, 0) in world, minus the offset: x = 2 in the project's own space.
  assert.ok(Math.abs(saved.target[0] - 2) < 1e-9 && Math.abs(saved.target[1]) < 1e-9);
  assert.ok(saved.position[2] > 0, "stood back along the direction the camera was looking");
  // The camera itself did not move at the end.
  assert.deepEqual(camera.position.toArray(), [0, 0, 50]);
});

test("a finished bulk load leaves a saved view alone, and an overtaken session ends nothing", () => {
  runtime().camera.current = new THREE.PerspectiveCamera(60, 1);
  const mine: LoadingView = { v: 1, translation: null, position: [1, 1, 1], target: [0, 0, 0], up: [0, 0, 1] };
  writeLoadingView("user:me", "assets:asp", mine);
  const first = beginBulkLoad("user:me", "assets:asp");
  const second = beginBulkLoad("user:me", "assets:asp");
  endBulkLoad(first);
  assert.equal(bulkLoadActive(), true, "the second load is still running");
  noteBulkModel(new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(9, 9, 9)));
  endBulkLoad(second);
  assert.deepEqual(readLoadingView("user:me", "assets:asp"), mine);
});
