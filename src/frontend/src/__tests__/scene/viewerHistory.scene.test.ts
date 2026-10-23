import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import * as THREE from "three";

import { useSelectedObjectStore } from "@/state/useSelectedObjectStore";
import { useViewerHistoryStore } from "@/state/viewerHistoryStore";
import { createViewerRuntime, mountViewerRuntime } from "@/state/viewerRuntime";
import { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { recordViewerAction, redoViewerAction, undoViewerAction } from "@/utils/scene/viewerHistory";
import { hideSelectedRanges, unhideAllRanges } from "@/utils/scene/visibility";

// Viewer operations against a real (headless) scene: each action is one history entry, and undo /
// redo restore both the hidden ranges and the selection.

function mesh(ids: string[]): CustomBatchedMesh {
  const pos: number[] = [];
  ids.forEach((_, i) => pos.push(i, 0, 0, i + 1, 0, 0, i + 1, 1, 0));
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
  const ranges = new Map<string, [number, number]>(ids.map((id, i) => [id, [i * 3, 3]]));
  return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "buf", true, null);
}

const runtime = createViewerRuntime();
mountViewerRuntime(runtime);
let m: CustomBatchedMesh;

const hist = () => useViewerHistoryStore.getState();
const sel = () => useSelectedObjectStore.getState();
const hidden = () => [...m.getHiddenRanges()].sort();
const selected = () => [...(sel().selectedObjects.get(m) ?? [])].sort();
const select = (ids: string[]) =>
  recordViewerAction("select", () => {
    sel().clearSelectedObjects();
    sel().addBatchofMeshes(ids.map((id) => [m, id] as [THREE.Object3D, string]));
  });

beforeEach(() => {
  const scene = new THREE.Scene();
  m = mesh(["a", "b", "c", "d"]);
  scene.add(m);
  (runtime.scene as { current: THREE.Scene | null }).current = scene;
  sel().clearSelectedObjects();
  hist().reset();
});

test("hiding a selection is one entry; undo brings back both the ranges and the selection", () => {
  select(["a", "b"]);
  hideSelectedRanges();
  assert.deepEqual(hidden(), ["a", "b"]);
  assert.deepEqual(selected(), [], "hiding clears the selection");
  assert.equal(hist().past.length, 2, "select + hide");
  assert.equal(hist().past[1].label, "hide 2 objects");

  assert.equal(undoViewerAction(), "hide 2 objects");
  assert.deepEqual(hidden(), []);
  assert.deepEqual(selected(), ["a", "b"]);

  assert.equal(redoViewerAction(), "hide 2 objects");
  assert.deepEqual(hidden(), ["a", "b"]);
  assert.deepEqual(selected(), []);
});

test("undo of show all hides again only what was hidden", () => {
  m.hideBatchDrawRange(["c"]);
  select(["a"]);
  hideSelectedRanges();
  unhideAllRanges();
  assert.deepEqual(hidden(), []);
  undoViewerAction();
  assert.deepEqual(hidden(), ["a", "c"]);
  undoViewerAction();
  assert.deepEqual(hidden(), ["c"], "the range hidden outside the history stays hidden");
});

test("an action that changes nothing records nothing", () => {
  unhideAllRanges();
  hideSelectedRanges();
  assert.equal(hist().past.length, 0);
});

test("a nested recorded action is part of the outer one", () => {
  select(["a"]);
  recordViewerAction("hide", () => {
    hideSelectedRanges();
    m.hideBatchDrawRange(["d"]);
  });
  assert.equal(hist().past.length, 2);
  undoViewerAction();
  assert.deepEqual(hidden(), []);
});

test("undoing and redoing does not record", () => {
  select(["a"]);
  select(["b"]);
  undoViewerAction();
  assert.deepEqual(selected(), ["a"]);
  assert.equal(hist().past.length, 1);
  assert.equal(hist().future.length, 1);
  redoViewerAction();
  assert.deepEqual(selected(), ["b"]);
  assert.equal(hist().past.length, 2);
});

test("coalesced selection changes are one entry", () => {
  const box = (ids: string[]) =>
    recordViewerAction("select", () => {
      sel().clearSelectedObjects();
      sel().addBatchofMeshes(ids.map((id) => [m, id] as [THREE.Object3D, string]));
    }, { coalesce: "box:1" });
  box(["a"]);
  box(["a", "b"]);
  box(["a", "b", "c"]);
  assert.equal(hist().past.length, 1);
  assert.equal(hist().past[0].label, "select 3 objects");
  undoViewerAction();
  assert.deepEqual(selected(), []);
  redoViewerAction();
  assert.deepEqual(selected(), ["a", "b", "c"]);
});

test("an entry leaves alone a mesh no longer in the scene", () => {
  select(["a"]);
  hideSelectedRanges();
  m.removeFromParent();
  undoViewerAction();
  assert.deepEqual(hidden(), ["a"]);
  assert.deepEqual(selected(), []);
});
