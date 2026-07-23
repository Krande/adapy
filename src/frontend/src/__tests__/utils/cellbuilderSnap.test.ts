import assert from "node:assert/strict";
import {test} from "node:test";

import {applyFaceOffset, boxCorners, quantize, snapBox, snapToVertices} from "../../utils/cellbuilder/snap";

test("quantize rounds to the step", () => {
    assert.equal(quantize(1.23, 0.1), 1.2);
    assert.equal(quantize(1.27, 0.1), 1.3);
    assert.equal(quantize(5, 0), 5);
});

test("boxCorners yields the 8 corners", () => {
    const corners = boxCorners({origin: [0, 0, 0], size: [1, 2, 3]});
    assert.equal(corners.length, 8);
    assert.deepEqual(corners[0], [0, 0, 0]);
    assert.ok(corners.some((c) => c[0] === 1 && c[1] === 2 && c[2] === 3));
});

test("snapToVertices picks the nearest pair under threshold", () => {
    const candidate = boxCorners({origin: [1.1, 0, 0], size: [1, 1, 1]});
    const existing = boxCorners({origin: [0, 0, 0], size: [1, 1, 1]});
    // candidate min corner (1.1,0,0) is 0.1 from existing corner (1,0,0)
    const delta = snapToVertices(candidate, existing, 0.25);
    assert.ok(delta !== null);
    assert.ok(Math.abs(delta![0] + 0.1) < 1e-9);
    assert.equal(delta![1], 0);
    assert.equal(delta![2], 0);
});

test("snapToVertices returns null outside threshold", () => {
    const candidate = boxCorners({origin: [5, 5, 5], size: [1, 1, 1]});
    const existing = boxCorners({origin: [0, 0, 0], size: [1, 1, 1]});
    assert.equal(snapToVertices(candidate, existing, 0.25), null);
});

test("snapBox attaches a nearby box magnetically", () => {
    const snapped = snapBox(
        {origin: [1.08, 0.05, -0.03], size: [1, 1, 1]},
        [{origin: [0, 0, 0], size: [1, 1, 1]}],
        0.25,
    );
    assert.ok(Math.abs(snapped.origin[0] - 1) < 1e-9);
    assert.ok(Math.abs(snapped.origin[1]) < 1e-9);
    assert.ok(Math.abs(snapped.origin[2]) < 1e-9);
});

test("applyFaceOffset grows a positive face", () => {
    const out = applyFaceOffset({origin: [0, 0, 0], size: [2, 2, 2]}, 0, true, 0.5);
    assert.deepEqual(out.origin, [0, 0, 0]);
    assert.deepEqual(out.size, [2.5, 2, 2]);
});

test("applyFaceOffset moves the origin for a negative face", () => {
    const out = applyFaceOffset({origin: [0, 0, 0], size: [2, 2, 2]}, 1, false, 0.5);
    assert.deepEqual(out.origin, [0, 0.5, 0]);
    assert.deepEqual(out.size, [2, 1.5, 2]);
});

test("applyFaceOffset clamps to minSize", () => {
    const shrunk = applyFaceOffset({origin: [0, 0, 0], size: [1, 1, 1]}, 2, true, -5, 0.1);
    assert.ok(Math.abs(shrunk.size[2] - 0.1) < 1e-9);
    const negShrunk = applyFaceOffset({origin: [0, 0, 0], size: [1, 1, 1]}, 2, false, 5, 0.1);
    assert.ok(Math.abs(negShrunk.size[2] - 0.1) < 1e-9);
    assert.ok(Math.abs(negShrunk.origin[2] - 0.9) < 1e-9);
});
