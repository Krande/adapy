// `ctx.scene.pickObjects` (plugin API 1.26.0): a plugin holding the scene's clicks.
import assert from "node:assert/strict";
import { test } from "node:test";

import { endScenePicks, offerScenePick, pickObjects, scenePicksHeld, type ScenePick } from "@/plugins/scenePicks";

const beam = (name: string): ScenePick => ({
  model: "m1",
  id: `r-${name}`,
  name,
  sourceName: "model.glb",
  point: [1, 2, 3],
  additive: false,
});

test("nobody holds a pick: a click is core's", () => {
  assert.equal(scenePicksHeld(), false);
  assert.equal(offerScenePick(beam("b1")), false);
});

test("a held pick is handed each click, which is then not core's, until it is ended", () => {
  const seen: string[] = [];
  const session = pickObjects("p", { onPick: (pick) => void seen.push(pick.name ?? "") });
  assert.equal(scenePicksHeld(), true);
  assert.equal(offerScenePick(beam("b1")), true);
  assert.equal(offerScenePick(beam("b2")), true);
  assert.deepEqual(seen, ["b1", "b2"]);
  session.end();
  session.end(); // twice is once
  assert.equal(scenePicksHeld(), false);
  assert.equal(offerScenePick(beam("b3")), false);
  assert.deepEqual(seen, ["b1", "b2"]);
});

test("a click a holder lets through goes to whoever asked before, then to core", () => {
  const first: string[] = [];
  const second: string[] = [];
  const a = pickObjects("a", { onPick: (pick) => void first.push(pick.name ?? "") });
  // Takes beams only, by name; the rest it lets through.
  const b = pickObjects("b", { onPick: (pick) => (pick.name?.startsWith("b") ? void second.push(pick.name) : false) });
  assert.equal(offerScenePick(beam("b1")), true);
  assert.equal(offerScenePick(beam("plate")), true);
  assert.deepEqual([first, second], [["plate"], ["b1"]]);
  a.end();
  assert.equal(offerScenePick(beam("plate")), false); // nobody took it
  b.end();
});

test("removing an owner from the scene ends its picks, and a holder that throws loses its own", () => {
  pickObjects("gone", { onPick: () => {} });
  pickObjects("gone", { onPick: () => {} });
  const kept = pickObjects("kept", { onPick: () => {} });
  endScenePicks("gone");
  assert.equal(scenePicksHeld(), true);
  kept.end();
  assert.equal(scenePicksHeld(), false);

  const errors = console.error;
  console.error = () => {};
  try {
    pickObjects("broken", {
      onPick: () => {
        throw new Error("boom");
      },
    });
    assert.equal(offerScenePick(beam("b1")), false);
    assert.equal(scenePicksHeld(), false);
  } finally {
    console.error = errors;
  }
});
