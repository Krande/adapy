import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import type { FeaSessionHandle } from "../../state/modelSession";
import { useFeaAnimationStore } from "../../state/feaAnimationStore";
import {
  fieldHasResultPointMarkers,
  installResultPointMarkers,
  resultPointMarkers,
  resultPointMarkersShown,
} from "../../utils/scene/fea/resultPointMarkers";
import { feaSession } from "../../utils/scene/fea/streaming/session";
import {
  setFeaResultColorsVisible,
  setFeaResultPointMarkersVisible,
  syncFeaOverlayVisibility,
} from "../../utils/scene/fea/streaming/visibility";
import { SceneSync } from "../../viewport/adacpp/sceneSync";
import { Topology, type AdacppModule, type WebRendererApi } from "../../viewport/adacpp/types";

// The result-point markers of a result-point field can be switched off. The switch
// is a flag on the markers, kept across the repaints that rebuild them.

function install(mesh: THREE.Mesh): void {
  installResultPointMarkers(mesh, new Float32Array([0, 0, 0, 1, 0, 0]), new Float32Array(6), new Float32Array(6));
}

function setup(): { mesh: THREE.Mesh; repaints: number[] } {
  const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  feaSession.active = { mesh, resultColorsShown: true } as unknown as FeaSessionHandle;
  const repaints: number[] = [];
  useFeaAnimationStore.setState({
    resultColorsVisible: true,
    resultPointMarkersVisible: true,
    applyStep: async (step: number) => {
      repaints.push(step);
    },
  } as never);
  return { mesh, repaints };
}

test("only result-point fields offer the markers toggle", () => {
  assert.equal(fieldHasResultPointMarkers({ support: "result_point" } as never), true);
  assert.equal(fieldHasResultPointMarkers({ support: "line_result_point" } as never), true);
  assert.equal(fieldHasResultPointMarkers({ support: "element_nodal" } as never), false);
  assert.equal(fieldHasResultPointMarkers({} as never), false);
  assert.equal(fieldHasResultPointMarkers(null), false);
});

test("markers show only while result colours show and the user wants them", () => {
  assert.equal(resultPointMarkersShown(true, true), true);
  assert.equal(resultPointMarkersShown(true, false), false);
  assert.equal(resultPointMarkersShown(false, true), false);
});

test("the preference defaults on and survives a scene reset", () => {
  assert.equal(useFeaAnimationStore.getInitialState().resultPointMarkersVisible, true);
  useFeaAnimationStore.getState().setResultPointMarkersVisible(false);
  useFeaAnimationStore.getState().reset();
  assert.equal(useFeaAnimationStore.getState().resultPointMarkersVisible, false);
  useFeaAnimationStore.getState().setResultPointMarkersVisible(true);
});

test("switching the markers off hides them without a repaint", async () => {
  const { mesh, repaints } = setup();
  install(mesh);
  assert.equal(resultPointMarkers(mesh)?.visible, true);
  setFeaResultPointMarkersVisible(false);
  assert.equal(useFeaAnimationStore.getState().resultPointMarkersVisible, false);
  assert.equal(resultPointMarkers(mesh)?.visible, false);
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(repaints, []);
  setFeaResultPointMarkersVisible(true);
  assert.equal(resultPointMarkers(mesh)?.visible, true);
  feaSession.active = null;
});

test("markers rebuilt by a step or field change stay hidden", () => {
  const { mesh } = setup();
  setFeaResultPointMarkersVisible(false);
  install(mesh);
  assert.equal(resultPointMarkers(mesh)?.visible, false);
  syncFeaOverlayVisibility();
  assert.equal(resultPointMarkers(mesh)?.visible, false);
  setFeaResultPointMarkersVisible(true);
  feaSession.active = null;
});

test("result colours off hides the markers; back on restores the user's choice", () => {
  const { mesh } = setup();
  install(mesh);
  setFeaResultColorsVisible(false);
  assert.equal(resultPointMarkers(mesh)?.visible, false);
  setFeaResultColorsVisible(true);
  assert.equal(resultPointMarkers(mesh)?.visible, true);
  setFeaResultPointMarkersVisible(false);
  setFeaResultColorsVisible(true);
  assert.equal(resultPointMarkers(mesh)?.visible, false);
  setFeaResultPointMarkersVisible(true);
  feaSession.active = null;
});

test("the WebGPU mirror drops hidden markers and brings them back when shown", () => {
  const heap = new ArrayBuffer(1 << 16);
  let top = 16;
  const module = {
    HEAPU8: new Uint8Array(heap),
    HEAPU32: new Uint32Array(heap),
    HEAPF32: new Float32Array(heap),
    _malloc: (n: number) => {
      const p = top;
      top += (n + 15) & ~15;
      return p;
    },
    _free: () => {},
  } as unknown as AdacppModule;
  const live = new Map<number, number>();
  let next = 0;
  const renderer = {
    addGeometry: (topology: number) => {
      const id = next++;
      live.set(id, topology);
      return id;
    },
    addInstance: () => 0,
    setInstance: () => {},
    setSegmentStates: () => {},
    removeGeometry: (g: number) => live.delete(g),
    removeInstance: () => {},
    updateColors: () => {},
    updateMorph: () => {},
    setClipPlanes: () => {},
    setCamera: () => {},
    setBackground: () => {},
    setLighting: () => {},
  } as unknown as WebRendererApi;
  const points = () => [...live.values()].filter((t) => t === Topology.Points).length;

  const { mesh } = setup();
  install(mesh);
  const scene = new THREE.Scene();
  scene.add(mesh);
  const camera = new THREE.PerspectiveCamera();
  camera.layers.enableAll();
  const sync = new SceneSync(module, renderer);
  sync.sync(scene, camera);
  assert.equal(points(), 1);
  setFeaResultPointMarkersVisible(false);
  sync.sync(scene, camera);
  assert.equal(points(), 0);
  setFeaResultPointMarkersVisible(true);
  sync.sync(scene, camera);
  assert.equal(points(), 1);
  feaSession.active = null;
});
