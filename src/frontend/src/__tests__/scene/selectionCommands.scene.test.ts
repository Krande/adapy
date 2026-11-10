import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import * as THREE from "three";

import { useObjectInfoStore } from "@/state/objectInfoStore";
import { useSelectedObjectStore } from "@/state/useSelectedObjectStore";
import { useViewerHistoryStore } from "@/state/viewerHistoryStore";
import { createViewerRuntime, mountViewerRuntime } from "@/state/viewerRuntime";
import { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { invertSelection, selectAllRanges } from "@/utils/scene/selectionCommands";
import { undoViewerAction } from "@/utils/scene/viewerHistory";
import { createThreeViewport } from "@/viewport/api/threeViewport";
import { setActiveViewport } from "@/viewport/api/viewportApi";

// Select all / invert against a real (headless) scene: hidden ranges and switched-off models are
// left out, a wrapper-keyed selection is inverted as its meshes', and each is one undo entry.

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
// The history asks the viewport on screen what is selected and hidden: the three.js one, over this scene.
setActiveViewport(createThreeViewport({} as never));
let a: CustomBatchedMesh;
let b: CustomBatchedMesh;
let off: CustomBatchedMesh;
let wrapper: THREE.Group;

const sel = () => useSelectedObjectStore.getState();
const ids = (m: THREE.Object3D) => [...(sel().selectedObjects.get(m) ?? [])].sort();

beforeEach(() => {
  const scene = new THREE.Scene();
  a = mesh(["a1", "a2", "a3"]);
  b = mesh(["b1", "b2"]);
  off = mesh(["o1"]);
  wrapper = new THREE.Group();
  wrapper.add(b);
  const offModel = new THREE.Group();
  offModel.visible = false;
  offModel.add(off);
  scene.add(a, wrapper, offModel);
  (runtime.scene as { current: THREE.Scene | null }).current = scene;
  sel().clearSelectedObjects();
  useViewerHistoryStore.getState().reset();
});

test("select all: every shown element, one undo entry, named in the info panel", () => {
  a.hideBatchDrawRange(new Set(["a2"]));
  useViewerHistoryStore.getState().reset();
  assert.equal(selectAllRanges(), 4);
  assert.deepEqual(ids(a), ["a1", "a3"]);
  assert.deepEqual(ids(b), ["b1", "b2"]);
  assert.deepEqual(ids(off), [], "a switched-off model is not taken");
  assert.equal(useObjectInfoStore.getState().name, "All: 4 elements");
  assert.equal(useViewerHistoryStore.getState().past.length, 1);
  undoViewerAction();
  assert.equal(sel().selectedObjects.size, 0);
});

test("invert: among the shown elements, a wrapper-keyed selection folded onto its meshes", () => {
  // b1 selected through the wrapper the tree keys by.
  sel().addBatchofMeshes([[a, "a1"], [wrapper, "b1"]]);
  useViewerHistoryStore.getState().reset();
  assert.equal(invertSelection(), 3);
  assert.deepEqual(ids(a), ["a2", "a3"]);
  assert.deepEqual(ids(b), ["b2"]);
  assert.deepEqual(ids(wrapper), [], "the result is keyed by the meshes");
  assert.equal(useViewerHistoryStore.getState().past.length, 1);
  undoViewerAction();
  assert.deepEqual(ids(a), ["a1"]);
});
