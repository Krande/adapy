import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import type { FeaSessionHandle } from "../../state/modelSession";
import { useFeaAnimationStore } from "../../state/feaAnimationStore";
import { RESULTS_OWNER, useSceneColorOwnerStore } from "../../state/sceneColorOwnerStore";
import { _resetSceneColorOwnerForTests, notifyActiveModeSceneColor } from "../../utils/scene/fea/modeSceneColor";
import { feaSession } from "../../utils/scene/fea/streaming/session";
import {
  notePaintedShown,
  noteShownMeasure,
  scheduleShownRangeRepaint,
} from "../../utils/scene/fea/streaming/shownRangeRepaint";

// Switching the beam solids on or off changes what is drawn, so a scale measured
// over the drawing is measured again. A repaint is every surface's colours, markers
// and lines; it is only needed when that measure moved.

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

function setup(): { solids: THREE.Mesh; repaints: number[] } {
  const solids = new THREE.Mesh();
  solids.visible = false;
  feaSession.active = { mesh: new THREE.Mesh(), beamSolidMesh: solids } as unknown as FeaSessionHandle;
  const repaints: number[] = [];
  useFeaAnimationStore.setState({
    sessionActive: true,
    stepIndex: 3,
    applyStep: async (step: number) => {
      repaints.push(step);
    },
  } as never);
  return { solids, repaints };
}

test("toggling the solids without moving the measured range does not repaint", async () => {
  const { solids, repaints } = setup();
  noteShownMeasure(() => [0, 10]);
  notePaintedShown(true, [0, 10]);
  solids.visible = true;
  scheduleShownRangeRepaint();
  await tick();
  assert.deepEqual(repaints, []);
});

test("toggling the solids when the measured range moves repaints the step", async () => {
  const { solids, repaints } = setup();
  let hi = 10;
  noteShownMeasure(() => [0, hi]);
  notePaintedShown(true, [0, 10]);
  hi = 12;
  solids.visible = true;
  scheduleShownRangeRepaint();
  await tick();
  assert.deepEqual(repaints, [3]);
});

test("a paint without a measure repaints as before", async () => {
  const { solids, repaints } = setup();
  notePaintedShown(true, [0, 10]);
  solids.visible = true;
  scheduleShownRangeRepaint();
  await tick();
  assert.deepEqual(repaints, [3]);
});

test("a scale not measured over the drawing never repaints", async () => {
  const { solids, repaints } = setup();
  noteShownMeasure(() => [0, 99]);
  notePaintedShown(false, null);
  solids.visible = true;
  scheduleShownRangeRepaint();
  await tick();
  assert.deepEqual(repaints, []);
  feaSession.active = null;
});

// Isolating a set only changes what is visible. Under a mode that owns the scene colouring the field
// stays suspended: the repaint (result colours, legend) waits until that mode ends, then runs once,
// with the isolation still in force.
test("a mode that owns the colour is not repainted under; leaving it repaints once", async () => {
  const { solids, repaints } = setup();
  _resetSceneColorOwnerForTests();
  notePaintedShown(true, [0, 10]);
  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  assert.notEqual(useSceneColorOwnerStore.getState().activeOwner(), RESULTS_OWNER);
  solids.visible = true; // what is drawn changed (an isolation bumps the same key)
  scheduleShownRangeRepaint();
  await tick();
  assert.deepEqual(repaints, [], "no repaint under the owning mode");
  notifyActiveModeSceneColor(null);
  await tick();
  assert.deepEqual(repaints, [3], "one repaint when the owner ends");
  _resetSceneColorOwnerForTests();
  feaSession.active = null;
});

test("a mode that does not own the colour repaints as before", async () => {
  const { solids, repaints } = setup();
  _resetSceneColorOwnerForTests();
  notePaintedShown(true, [0, 10]);
  notifyActiveModeSceneColor({ id: "inspect" });
  solids.visible = true;
  scheduleShownRangeRepaint();
  await tick();
  assert.deepEqual(repaints, [3]);
  feaSession.active = null;
});
