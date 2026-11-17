// Buttons a shell or a plugin adds to the playback transport (layout/playbackActions.ts).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  listPlaybackActions,
  notifyPlaybackActionsChanged,
  playbackActionState,
  playbackActionsVersion,
  registerPlaybackAction,
  resetPlaybackActions,
  subscribePlaybackActions,
  unregisterPlaybackAction,
} from "@/layout/playbackActions";

test("actions are listed in order, replaced by id, and their listeners hear every change", () => {
  resetPlaybackActions();
  let heard = 0;
  const off = subscribePlaybackActions(() => heard++);
  const before = playbackActionsVersion();
  registerPlaybackAction({ id: "p:depth", label: "Depth", order: 2, run: () => {} }, "p");
  registerPlaybackAction({ id: "p:detail", label: "Detail", order: 1, run: () => {} }, "p");
  registerPlaybackAction({ id: "p:depth", label: "Depth camera", order: 2, run: () => {} }, "p");
  assert.deepEqual(listPlaybackActions().map((a) => [a.id, a.label]), [["p:detail", "Detail"], ["p:depth", "Depth camera"]]);
  notifyPlaybackActionsChanged();
  unregisterPlaybackAction("p:detail");
  unregisterPlaybackAction("p:detail"); // gone already: nothing to hear
  assert.deepEqual([heard, playbackActionsVersion() - before], [5, 5]);
  off();
  resetPlaybackActions();
  assert.equal(heard, 5);
});

test("an action is offered while `when` says so, pressed while `pressed` says so, and one that throws is left off", () => {
  let offered = false;
  let on = false;
  const toggle = { id: "t", label: "T", when: () => offered, pressed: () => on, run: () => void (on = !on) };
  assert.equal(playbackActionState(toggle), null);
  offered = true;
  assert.deepEqual(playbackActionState(toggle), { pressed: false });
  toggle.run();
  assert.deepEqual(playbackActionState(toggle), { pressed: true });
  // A plain button has no pressed state to announce.
  assert.deepEqual(playbackActionState({ id: "b", label: "B", run: () => {} }), { pressed: undefined });
  const errors = console.error;
  console.error = () => {};
  try {
    assert.equal(playbackActionState({ id: "x", label: "X", when: () => { throw new Error("boom"); }, run: () => {} }), null);
  } finally {
    console.error = errors;
  }
});
