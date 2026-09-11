import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  RESULTS_OWNER,
  useSceneColorOwnerStore,
  type SceneColorView,
} from "../../state/sceneColorOwnerStore";

// The bookkeeping under the mode arbiter, pinned on its own. Nothing here
// touches the FEA or legend stores: the store is handed a snapshot of what is
// on screen and hands back whose view it is, which is the whole point of
// making ownership explicit — it can be reasoned about without a scene.
//
// `modeSceneColor.test.ts` pins the same rules from the outside, as the mode
// transitions a shell actually reports.

const s = () => useSceneColorOwnerStore.getState();
const ids = () => s().stack.map((entry) => entry.id);

/** A view as the arbiter's `snapshot()` would build it. */
function view(fieldName: string | null, legendMin = 0, legendMax = 1): SceneColorView {
  return {
    fieldName,
    reduction: "VONMISES",
    stepIndex: 0,
    layer: null,
    legendShown: true,
    legendMin,
    legendMax,
  };
}

/** What the user had on screen in Results before any mode was entered. */
const userView = view("sesam.elements.g_stress", 5, 50);

beforeEach(() => {
  s().reset();
});

test("the stack opens with results alone, and results never pushes", () => {
  assert.deepEqual(ids(), [RESULTS_OWNER]);
  assert.equal(s().activeOwner(), RESULTS_OWNER);

  s().push(RESULTS_OWNER, userView);
  assert.deepEqual(ids(), [RESULTS_OWNER]);
  assert.equal(s().pop(userView), null);
});

test("two owning modes push and pop in order, each keeping its own view", () => {
  const entered = s().push("capacity", userView);
  // Nothing of its own yet, so the caller suspends rather than restoring.
  assert.equal(entered.view, null);
  assert.deepEqual(ids(), [RESULTS_OWNER, "capacity"]);
  assert.equal(s().activeOwner(), "capacity");

  // Capacity paints its own legend, then the shell switches straight to a
  // second owning mode.
  s().markPainted("capacity");
  const capacityView = view("sesam.elements.g_stress", 0, 1.2);
  const second = s().switchTop("inspect", capacityView);
  assert.equal(second.view, null); // inspect has painted nothing of its own
  assert.deepEqual(ids(), [RESULTS_OWNER, "inspect"]);

  // Leaving inspect puts the USER's view back, not capacity's: owner-to-owner
  // left the entry underneath untouched.
  const popped = s().pop(capacityView);
  assert.equal(popped?.left.id, "inspect");
  assert.equal(popped?.left.view, null);
  assert.deepEqual(popped?.below.view, userView);
  assert.deepEqual(ids(), [RESULTS_OWNER]);

  // And capacity still gets its own legend back when it is entered again.
  assert.deepEqual(s().push("capacity", userView).view, capacityView);
});

test("a load tagged results while a mode owns is suspended and set aside under it", () => {
  s().push("capacity", view(null, 5, 50)); // the page opened straight into the mode

  const landed = view("sesam.nodes.displacement", 0, 39);
  assert.equal(s().noteLoad("model.SIN", RESULTS_OWNER, landed), true);
  assert.equal(s().activeOwner(), "capacity");

  const popped = s().pop(landed);
  assert.equal(popped?.left.view, null); // the mode painted nothing over it
  assert.deepEqual(popped?.below.view, landed); // the user's result comes back
});

test("a load tagged by the owning mode is its own painting", () => {
  s().noteLoad("model.SIN", RESULTS_OWNER, userView); // the page opened on this model
  assert.equal(s().requestingOwner("model.SIN"), RESULTS_OWNER); // no owning mode

  s().push("inspect", userView);
  // A repaint of the source on screen is the mode's; another model is the user's.
  assert.equal(s().requestingOwner("model.SIN"), "inspect");
  assert.equal(s().requestingOwner("other.SIN"), RESULTS_OWNER);
  assert.equal(s().requestingOwner(null), RESULTS_OWNER);

  const painted = view("props.material", 1, 3);
  assert.equal(s().noteLoad("model.SIN", "inspect", painted), false);

  const popped = s().pop(painted);
  assert.deepEqual(popped?.left.view, painted);
  assert.deepEqual(s().parked["inspect"], painted);
});

test("a paint counts for the owner that tagged it, and only on top", () => {
  s().push("capacity", userView);

  // A tag from a mode that is not on top is not capacity's painting.
  s().markPainted("inspect");
  assert.equal(s().stack[1].painted, false);

  s().markPainted("capacity");
  const own = view("sesam.elements.g_stress", 0, 1.2);
  assert.deepEqual(s().pop(own)?.left.view, own);

  // Outside an owning mode there is nothing to record.
  s().markPainted(RESULTS_OWNER);
  assert.deepEqual(ids(), [RESULTS_OWNER]);
});

test("clearing the model drops every saved view but keeps the stack", () => {
  s().push("capacity", userView);
  s().markPainted("capacity");
  s().switchTop("inspect", view("sesam.elements.g_stress", 0, 1.2)); // parks capacity
  s().noteLoad("model.SIN", "inspect", view("props.material", 1, 3));

  s().clearViews();

  // The mode the user is in stays the mode they are in; what it was showing
  // belonged to a model that is gone.
  assert.deepEqual(ids(), [RESULTS_OWNER, "inspect"]);
  assert.deepEqual(
    s().stack.map((entry) => entry.view),
    [null, null],
  );
  assert.equal(s().stack[1].painted, false);
  assert.deepEqual(s().parked, {});
  assert.equal(s().loadedSource, null);
  // So the same file reopened is a new source — the user's, not a repaint.
  assert.equal(s().requestingOwner("model.SIN"), RESULTS_OWNER);
});

test("a results field pick in flight when a mode is entered lands as the user's", () => {
  s().noteLoad("model.SIN", RESULTS_OWNER, userView);

  // The user picks another field in Results. The tag is taken when the load is
  // REQUESTED, with no owning mode on the stack.
  const tag = s().requestingOwner("model.SIN");
  assert.equal(tag, RESULTS_OWNER);

  // The owning mode is entered while the fetch is still in flight...
  s().push("capacity", userView);
  // ...and only then does the field land.
  const landed = view("sesam.nodes.displacement", 0, 39);
  assert.equal(s().noteLoad("model.SIN", tag, landed), true);

  // Not credited to the mode: it asked for nothing and has painted nothing.
  assert.equal(s().stack[1].id, "capacity");
  assert.equal(s().stack[1].painted, false);

  const popped = s().pop(landed);
  assert.equal(popped?.left.view, null);
  assert.deepEqual(popped?.below.view, landed);
  // Nothing was parked for it, so entering it again suspends fresh.
  assert.equal(s().push("capacity", landed).view, null);
});
