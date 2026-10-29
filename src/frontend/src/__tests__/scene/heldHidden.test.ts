import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import { holdHidden, revealHeld, takeIfHeld } from "@/utils/scene/heldHidden";

// A whole site loaded only to show the part a selection box holds joins the scene invisible, and is
// shown once the rest is hidden by row -- never drawn whole in between.

test("a held subject's whole model joins invisible and is shown on reveal", () => {
  holdHidden("p", "c", "site-1");
  const group = new THREE.Group();
  assert.equal(takeIfHeld("assets:p/c/site-1@r1", group), true);
  assert.equal(group.visible, false);
  revealHeld("p", "c", "site-1");
  assert.equal(group.visible, true);
  // Released: the next load of it is drawn as usual.
  const again = new THREE.Group();
  assert.equal(takeIfHeld("assets:p/c/site-1@r2", again), false);
  assert.equal(again.visible, true);
});

test("a one-node model of a held subject, another subject, and a file are not held", () => {
  holdHidden("p", "c", "site-2");
  for (const name of ["assets:p/c/site-2@r1#node-9", "assets:p/c/site-3@r1", "models/site-2.glb"]) {
    const g = new THREE.Group();
    assert.equal(takeIfHeld(name, g), false, name);
    assert.equal(g.visible, true, name);
  }
  revealHeld("p", "c", "site-2");
});
