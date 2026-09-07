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

// Coming BACK to an owning mode. Suspending is right the first time — the mode
// has painted nothing yet — and wrong every time after: colour by material in
// Inspect, glance at Results, come back, and the material colouring was gone,
// because every entry was treated as a first entry.

test("re-entering an owning mode puts back what it was showing", () => {
  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  assert.equal(useColorStore.getState().showLegend, false); // first entry suspends

  // The property painter loads its own field and shows its own legend.
  useFeaAnimationStore.setState({ fieldName: "props.material", stepIndex: 0 });
  useColorStore.setState({ min: 1, max: 3, showLegend: true });

  notifyActiveModeSceneColor({ id: "results" });
  // Leaving reloads the user's field. As in the test above, the reselect needs a
  // session, so what is observable here is the step and layer going back.
  assert.equal(useFeaAnimationStore.getState().stepIndex, 3);
  // Stand in for that reload landing.
  useFeaAnimationStore.setState({ fieldName: "sesam.elements.g_stress" });

  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  // Not suspended this time: Inspect's own view is reloaded instead, so its step
  // comes back rather than the legend being hidden.
  assert.equal(sceneColorOwner(), "inspect");
  assert.equal(useFeaAnimationStore.getState().stepIndex, 0);
});

test("a mode that painted nothing still suspends every time", () => {
  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  notifyActiveModeSceneColor({ id: "results" });
  useColorStore.setState({ showLegend: true });
  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  // Nothing of its own to show, so the field is set aside as before.
  assert.equal(useColorStore.getState().showLegend, false);
});

test("two owning modes remember their own views, not each other's", () => {
  notifyActiveModeSceneColor({ id: "inspect", ownsSceneColor: true });
  useFeaAnimationStore.setState({ fieldName: "props.material" });
  useColorStore.setState({ min: 1, max: 3, showLegend: true });

  notifyActiveModeSceneColor({ id: "capacity", ownsSceneColor: true });
  // Capacity has painted nothing yet, so it suspends rather than inheriting
  // Inspect's legend.
  assert.equal(useColorStore.getState().showLegend, false);
  assert.equal(sceneColorOwner(), "capacity");

  // And leaving still restores the user's ORIGINAL view, not either mode's.
  useFeaAnimationStore.setState({ fieldName: "sesam.elements.g_stress" });
  notifyActiveModeSceneColor({ id: "results" });
  assert.deepEqual(
    [useColorStore.getState().min, useColorStore.getState().max],
    [5, 50],
  );
});
