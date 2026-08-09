import assert from "node:assert/strict";
import {test} from "node:test";

import {
    applyFaceOffset,
    BOX_FACE_SIDES,
    boxCorners,
    edgeEndpoints,
    edgeHitOnFace,
    faceCenter,
    originFromCenter,
    placeInCell,
    quantize,
    snapBox,
    snapBoxTranslation,
    snapBoxTranslationDetail,
    snapToVertices,
    snapToVerticesAxis,
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

test("snapToVerticesAxis snaps only along the locked axis", () => {
    // candidate corner (1.1, 0.1, 0.1) is close to existing (1,0,0): free snap
    // would pull all three axes, but locked to X only the X delta is returned.
    const candidate = boxCorners({origin: [1.1, 0.1, 0.1], size: [1, 1, 1]});
    const existing = boxCorners({origin: [0, 0, 0], size: [1, 1, 1]});
    const delta = snapToVerticesAxis(candidate, existing, 0.25, 0);
    assert.ok(delta !== null);
    assert.ok(Math.abs(delta![0] + 0.1) < 1e-9); // -0.1 along X
    assert.equal(delta![1], 0);
    assert.equal(delta![2], 0);
});

test("snapToVerticesAxis returns null when the locked axis is out of range", () => {
    // X is aligned already (delta 0 is within range) — but check the miss case:
    // move far along the locked axis so nothing is reachable there.
    const candidate = boxCorners({origin: [5, 0, 0], size: [1, 1, 1]});
    const existing = boxCorners({origin: [0, 0, 0], size: [1, 1, 1]});
    assert.equal(snapToVerticesAxis(candidate, existing, 0.25, 0), null);
});

test("snapBoxTranslation: free snap aligns a corner in 3D", () => {
    const delta = snapBoxTranslation(
        {origin: [1.08, 0.05, -0.03], size: [1, 1, 1]},
        [{origin: [0, 0, 0], size: [1, 1, 1]}],
        0.25,
        null,
    );
    assert.ok(delta !== null);
    assert.ok(Math.abs(delta![0] + 0.08) < 1e-9);
    assert.ok(Math.abs(delta![1] + 0.05) < 1e-9);
    assert.ok(Math.abs(delta![2] - 0.03) < 1e-9);
});

test("snapBoxTranslation: axis-locked snap moves only that axis", () => {
    const delta = snapBoxTranslation(
        {origin: [1.08, 0.05, -0.03], size: [1, 1, 1]},
        [{origin: [0, 0, 0], size: [1, 1, 1]}],
        0.25,
        1, // lock Y
    );
    assert.ok(delta !== null);
    assert.equal(delta![0], 0);
    assert.ok(Math.abs(delta![1] + 0.05) < 1e-9);
    assert.equal(delta![2], 0);
});

test("snapBoxTranslationDetail reports the snapped-onto vertex (for the marker)", () => {
    // free 3D snap: candidate min corner (1.08,0.05,-0.03) snaps onto (1,0,0)
    const hit = snapBoxTranslationDetail(
        {origin: [1.08, 0.05, -0.03], size: [1, 1, 1]},
        [{origin: [0, 0, 0], size: [1, 1, 1]}],
        0.25,
        null,
    );
    assert.ok(hit !== null);
    assert.deepEqual(hit!.target, [1, 0, 0]);
    assert.ok(Math.abs(hit!.delta[0] + 0.08) < 1e-9);
});

test("snapBoxTranslationDetail axis-locked marks the matched neighbour corner", () => {
    const hit = snapBoxTranslationDetail(
        {origin: [1.1, 3, 3], size: [1, 1, 1]},
        [{origin: [0, 0, 0], size: [1, 1, 1]}],
        0.25,
        0, // lock X
    );
    assert.ok(hit !== null);
    // the target is an existing corner whose X (1) matched; marker sits there
    assert.equal(hit!.target[0], 1);
    assert.ok(Math.abs(hit!.delta[0] + 0.1) < 1e-9);
    assert.equal(hit!.delta[1], 0);
    assert.equal(hit!.delta[2], 0);
});

test("snapBoxTranslation: no neighbours in range yields null", () => {
    assert.equal(
        snapBoxTranslation(
            {origin: [10, 10, 10], size: [1, 1, 1]},
            [{origin: [0, 0, 0], size: [1, 1, 1]}],
            0.25,
            null,
        ),
        null,
    );
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

test("edgeHitOnFace finds the border edge, its run axis and bounded side", () => {
    const box = {origin: [0, 0, 0] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // top face (+Z, materialIndex 4); point near the y=0 border -> edge runs along X
    assert.deepEqual(edgeHitOnFace(box, 4, [2.5, 0.05, 3], 0.15), {axis: 0, boundaryAxis: 1, boundaryPositive: false});
    // top face, point near the x=5 border -> edge runs along Y
    assert.deepEqual(edgeHitOnFace(box, 4, [4.95, 2.0, 3], 0.15), {axis: 1, boundaryAxis: 0, boundaryPositive: true});
    // face interior -> null
    assert.equal(edgeHitOnFace(box, 4, [2.5, 2.0, 3], 0.15), null);
    // -X face (materialIndex 1); point near z=0 border -> edge runs along Y
    assert.deepEqual(edgeHitOnFace(box, 1, [0, 2.0, 0.1], 0.15), {axis: 1, boundaryAxis: 2, boundaryPositive: false});
});

test("edgeEndpoints derives world endpoints from the current box", () => {
    const box = {origin: [1, 2, 3] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // top face (+Z), edge along X bounding y at its high side
    const {start, end} = edgeEndpoints(box, 4, {axis: 0, boundaryAxis: 1, boundaryPositive: true});
    assert.deepEqual(start, [1, 6, 6]);
    assert.deepEqual(end, [6, 6, 6]);
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

test("faceCenter sits at each face centre (resize-handle placement)", () => {
    const box = {origin: [1, 2, 3] as [number, number, number], size: [4, 6, 8] as [number, number, number]};
    // +X / -X faces (materialIndex 0 / 1): pinned on X, centred in Y and Z.
    assert.deepEqual(faceCenter(box, 0), [5, 5, 7]);
    assert.deepEqual(faceCenter(box, 1), [1, 5, 7]);
    // +Z / -Z faces (materialIndex 4 / 5): pinned on Z, centred in X and Y.
    assert.deepEqual(faceCenter(box, 4), [3, 5, 11]);
    assert.deepEqual(faceCenter(box, 5), [3, 5, 3]);
});

test("placeInCell centres equipment on the footprint and seats it by surface/side", () => {
    const cell = {origin: [0, 0, 0] as [number, number, number], size: [5, 5, 3] as [number, number, number]};
    const size = [1, 1, 2] as [number, number, number];
    // roof + top: box sits on the roof (z = cell top), centred on the footprint
    assert.deepEqual(placeInCell(cell, size, "roof", "top", 0.1), [2, 2, 3]);
    // roof + bottom: box hangs from the roof (top at cell top => origin = 3 - 2)
    assert.deepEqual(placeInCell(cell, size, "roof", "bottom", 0.1), [2, 2, 1]);
    // floor + top: box stands on the floor
    assert.deepEqual(placeInCell(cell, size, "floor", "top", 0.1), [2, 2, 0]);
    // floor + bottom: box hangs under the floor (origin = 0 - 2)
    assert.deepEqual(placeInCell(cell, size, "floor", "bottom", 0.1), [2, 2, -2]);
    // X/Y centring is grid-quantized (odd footprint offset snaps to the step)
    const odd = {origin: [1, 1, 0] as [number, number, number], size: [3, 3, 3] as [number, number, number]};
    assert.deepEqual(placeInCell(odd, [1, 1, 1], "floor", "top", 0.5), [2, 2, 0]);
});

test("originFromCenter inverts the mesh centre, grid-quantized", () => {
    // Centre a 4x6x8 box on (5,5,7) -> origin (3,2,3).
    assert.deepEqual(originFromCenter([5, 5, 7], [4, 6, 8], 0.1), [3, 2, 3]);
    // A dragged centre off the grid snaps the origin back to the step.
    assert.deepEqual(originFromCenter([5.03, 5, 7], [4, 6, 8], 0.1), [3, 2, 3]);
    // Round-trips faceCenter's box centre back to its origin.
    const box = {origin: [1, 2, 3] as [number, number, number], size: [4, 6, 8] as [number, number, number]};
    const center = [
        box.origin[0] + box.size[0] / 2,
        box.origin[1] + box.size[1] / 2,
        box.origin[2] + box.size[2] / 2,
    ] as [number, number, number];
    assert.deepEqual(originFromCenter(center, box.size, 0.1), box.origin);
});
