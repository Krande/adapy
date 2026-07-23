import assert from "node:assert/strict";
import {test} from "node:test";

import {
    applyFaceOffset,
    BOX_FACE_SIDES,
    boxCorners,
    edgeHitOnFace,
    quantize,
    snapBox,
    snapToVertices,
    withAxisLength,
} from "../../utils/cellbuilder/snap";

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

test("BOX_FACE_SIDES follows BoxGeometry group order and the SE convention", () => {
    // materialIndex order +X,-X,+Y,-Y,+Z,-Z; SE: BOTTOM(-Z)=0..RIGHT(+X)=5
    assert.deepEqual(BOX_FACE_SIDES.map((s) => s.label), ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]);
    assert.deepEqual(BOX_FACE_SIDES.map((s) => s.se), [5, 4, 3, 2, 1, 0]);
});

test("edgeHitOnFace finds the border edge and its run axis", () => {
    const box = {origin: [0, 0, 0] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // top face (+Z, materialIndex 4); point near the y=0 border -> edge runs along X
    assert.deepEqual(edgeHitOnFace(box, 4, [2.5, 0.05, 3], 0.15), {axis: 0});
    // top face, point near the x=5 border -> edge runs along Y
    assert.deepEqual(edgeHitOnFace(box, 4, [4.95, 2.0, 3], 0.15), {axis: 1});
    // face interior -> null
    assert.equal(edgeHitOnFace(box, 4, [2.5, 2.0, 3], 0.15), null);
    // -X face (materialIndex 1); point near z=0 border -> edge runs along Y
    assert.deepEqual(edgeHitOnFace(box, 1, [0, 2.0, 0.1], 0.15), {axis: 1});
});

test("withAxisLength resizes one axis keeping the origin", () => {
    const out = withAxisLength({origin: [1, 2, 3], size: [5, 4, 3]}, 1, 6.5);
    assert.deepEqual(out.origin, [1, 2, 3]);
    assert.deepEqual(out.size, [5, 6.5, 3]);
    assert.equal(withAxisLength({origin: [0, 0, 0], size: [1, 1, 1]}, 0, -2, 0.1).size[0], 0.1);
});

test("applyFaceOffset clamps to minSize", () => {
    const shrunk = applyFaceOffset({origin: [0, 0, 0], size: [1, 1, 1]}, 2, true, -5, 0.1);
    assert.ok(Math.abs(shrunk.size[2] - 0.1) < 1e-9);
    const negShrunk = applyFaceOffset({origin: [0, 0, 0], size: [1, 1, 1]}, 2, false, 5, 0.1);
    assert.ok(Math.abs(negShrunk.size[2] - 0.1) < 1e-9);
    assert.ok(Math.abs(negShrunk.origin[2] - 0.9) < 1e-9);
});
