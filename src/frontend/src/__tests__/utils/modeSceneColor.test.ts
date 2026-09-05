import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  _resetSceneColorOwnerForTests,
  notifyActiveModeSceneColor,
  sceneColorOwner,
} from "../../utils/scene/fea/modeSceneColor";
import { useColorStore } from "../../state/colorLegendStore";
import { useFeaAnimationStore } from "../../state/feaAnimationStore";

// No FEA session is active in these tests, so the three.js side of the
// suspend (setFeaResultColorsVisible) is a safe no-op; what is being pinned is
// the arbiter's bookkeeping: who owns the colouring, what is saved, and what
// comes back on restore.

beforeEach(() => {
  _resetSceneColorOwnerForTests();
  useColorStore.setState({ min: 5, max: 50, showLegend: true });
  useFeaAnimationStore.setState({
    fieldName: "sesam.elements.g_stress",
    reduction: "VONMISES",
    stepIndex: 3,
    layer: "top",
    resultColorsVisible: true,
  });
});

test("a non-owning mode with nothing suspended is a no-op", () => {
  notifyActiveModeSceneColor({ id: "results" });
  assert.equal(sceneColorOwner(), null);
  assert.equal(useColorStore.getState().showLegend, true);
});

test("entering an owning mode hides the legend and records the owner", () => {
  notifyActiveModeSceneColor({ id: "capacity", ownsSceneColor: true });
  assert.equal(sceneColorOwner(), "capacity");
  assert.equal(useColorStore.getState().showLegend, false);
  // The user's own colour toggle is a preference, not part of the suspend.
  assert.equal(useFeaAnimationStore.getState().resultColorsVisible, true);
});

test("leaving restores the legend exactly, over whatever the mode painted", () => {
  notifyActiveModeSceneColor({ id: "capacity", ownsSceneColor: true });
  // The owning mode drives the shared legend through paintField.
  useColorStore.setState({ min: 0, max: 1, showLegend: true });

  notifyActiveModeSceneColor({ id: "results" });
  const legend = useColorStore.getState();
  assert.equal(sceneColorOwner(), null);
  assert.deepEqual([legend.min, legend.max, legend.showLegend], [5, 50, true]);
});

test("a mode that loaded another field gets the user's view put back", () => {
  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  // The property painter selected its own single-step field.
  useFeaAnimationStore.setState({
    fieldName: "props.plate_thickness",
    stepIndex: 0,
    layer: "mid",
  });

  notifyActiveModeSceneColor({ id: "results" });
  const fea = useFeaAnimationStore.getState();
  // The reselect goes through selectFeaResultComponent (no session here, so it
  // stops at the load), but the step and layer are already put back.
  assert.equal(fea.stepIndex, 3);
  assert.equal(fea.layer, "top");
  assert.equal(sceneColorOwner(), null);
});

test("owner-to-owner keeps the original snapshot", () => {
  notifyActiveModeSceneColor({ id: "capacity", ownsSceneColor: true });
  useColorStore.setState({ min: 0, max: 1 }); // capacity's legend
  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  assert.equal(sceneColorOwner(), "inspect");

  notifyActiveModeSceneColor({ id: "results" });
  const legend = useColorStore.getState();
  assert.deepEqual([legend.min, legend.max], [5, 50]);
});

test("null (no mode system) restores like any non-owning mode", () => {
  notifyActiveModeSceneColor({ id: "capacity", ownsSceneColor: true });
  notifyActiveModeSceneColor(null);
  assert.equal(sceneColorOwner(), null);
  assert.equal(useColorStore.getState().showLegend, true);
});
