// The orientation gizmo's picture for a camera.
import {test} from "node:test";
import assert from "node:assert/strict";

import {axisEnds} from "@/viewport/native/orientationGizmo";

test("looking down the Y axis, X is to the right, Z is up and Y points at the camera", () => {
    // From the front (-Y): the screen's right is +X, its up is +Z, and the camera sits towards -Y.
    const ends = axisEnds([1, 0, 0], [0, 0, 1], [0, -1, 0]);
    const by = Object.fromEntries(ends.map((e) => [e.label, e]));
    assert.deepEqual([by.X.x, by.X.y], [1, -0]);
    assert.deepEqual([by.Z.x, by.Z.y], [0, -1], "up on screen is negative y");
    assert.equal(by["-Y"].depth, 1, "the end facing the camera");
    assert.equal(by.Y.depth, -1);
    assert.equal(ends[ends.length - 1].label, "-Y", "the nearest end is drawn last, over the others");
    assert.deepEqual(by["-Y"].direction, [0, -1, 0], "clicking it looks from there");
});
