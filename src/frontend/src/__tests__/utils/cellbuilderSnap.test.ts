import assert from "node:assert/strict";
import {test} from "node:test";

import {
    applyFaceOffset,
    boundsToBox,
    BOX_FACE_SIDES,
    boxCorners,
    cadDisplayBox,
    cycleFaceIndex,
    edgeEndpoints,
    edgeHitOnFace,
    edgeIndexInFace,
    extrudeBox,
    faceCenter,
    faceEdges,
    farFaceAfterExtrude,
    neighbourFaceInDirection,
    openingBoxOnFace,
    originFromCenter,
    placeInCell,
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

test("extrudeBox grows a positive-face cell outward, same cross-section", () => {
    const box = {origin: [0, 0, 0] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // +X face (index 0), depth 2 -> new box at x in [5,7], y/z unchanged.
    const out = extrudeBox(box, 0, 2);
    assert.deepEqual(out.origin, [5, 0, 0]);
    assert.deepEqual(out.size, [2, 4, 3]);
});

test("extrudeBox on a negative face grows the other way", () => {
    const box = {origin: [0, 0, 0] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // -X face (index 1), depth 2 -> new box at x in [-2,0].
    const out = extrudeBox(box, 1, 2);
    assert.deepEqual(out.origin, [-2, 0, 0]);
    assert.deepEqual(out.size, [2, 4, 3]);
});

test("extrudeBox with negative depth flips the growth direction", () => {
    const box = {origin: [0, 0, 0] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // +X face but depth -2 -> grows inward, x in [3,5].
    const out = extrudeBox(box, 0, -2);
    assert.deepEqual(out.origin, [3, 0, 0]);
    assert.deepEqual(out.size, [2, 4, 3]);
});

test("farFaceAfterExtrude keeps the same face for positive depth, flips for negative", () => {
    assert.equal(farFaceAfterExtrude(0, 2), 0);
    assert.equal(farFaceAfterExtrude(0, -2), 1);
    assert.equal(farFaceAfterExtrude(2, 5), 2);
    assert.equal(farFaceAfterExtrude(3, -5), 2);
});

test("cycleFaceIndex wraps 0..5 both directions", () => {
    assert.equal(cycleFaceIndex(0, 1), 1);
    assert.equal(cycleFaceIndex(5, 1), 0);
    assert.equal(cycleFaceIndex(0, -1), 5);
    assert.equal(cycleFaceIndex(-1, 1), 0);
});

test("faceEdges yields 4 distinct border edges running in the face plane", () => {
    // +Z face (index 4): in-plane axes are X and Y; edges run along X or Y.
    const edges = faceEdges(4);
    assert.equal(edges.length, 4);
    for (const e of edges) assert.notEqual(e.axis, 2); // never along the face normal
    // round-trips through edgeIndexInFace
    edges.forEach((e, i) => assert.equal(edgeIndexInFace(4, e), i));
    assert.equal(edgeIndexInFace(4, {axis: 0, boundaryAxis: 0, boundaryPositive: true}), -1);
});

test("openingBoxOnFace straddles a +Z face by ±depth, sized in the face plane", () => {
    const cell = {origin: [0, 0, 0] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // +Z face (index 4): in-plane axes X,Y; face plane at z=3.
    const box = openingBoxOnFace(cell, 4, 1, 2, 1.5, 0.8, 1);
    assert.deepEqual(box.origin, [1, 2, 2]); // x=0+1, y=0+2, z=facePos(3)-depth(1)
    assert.deepEqual(box.size, [1.5, 0.8, 2]); // width, height, 2*depth
});

test("openingBoxOnFace maps 2D local to the +X face's in-plane axes (Y,Z)", () => {
    const cell = {origin: [0, 0, 0] as [number, number, number], size: [5, 4, 3] as [number, number, number]};
    // +X face (index 0): in-plane axes Y (localX) and Z (localY); plane at x=5.
    const box = openingBoxOnFace(cell, 0, 1, 0.5, 2, 1, 0.25);
    assert.deepEqual(box.origin, [4.75, 1, 0.5]); // x=facePos(5)-0.25, y=0+1, z=0+0.5
    assert.deepEqual(box.size, [0.5, 2, 1]); // 2*depth along X, width along Y, height along Z
});

test("neighbourFaceInDirection walks +Z face spatially (camera down -Z)", () => {
    // Camera looking down -Z: screen-right = +X, screen-up = +Y.
    const R: [number, number, number] = [1, 0, 0];
    const U: [number, number, number] = [0, 1, 0];
    // Faces: 0:+X 1:-X 2:+Y 3:-Y 4:+Z 5:-Z. Current = +Z (4).
    assert.equal(neighbourFaceInDirection(4, "right", R, U), 0); // +X
    assert.equal(neighbourFaceInDirection(4, "left", R, U), 1); // -X
    assert.equal(neighbourFaceInDirection(4, "up", R, U), 2); // +Y
    assert.equal(neighbourFaceInDirection(4, "down", R, U), 3); // -Y
});

test("neighbourFaceInDirection never returns the current or opposite face", () => {
    const R: [number, number, number] = [1, 0, 0];
    const U: [number, number, number] = [0, 1, 0];
    for (const dir of ["up", "down", "left", "right"] as const) {
        const n = neighbourFaceInDirection(4, dir, R, U);
        assert.notEqual(n, 4); // not itself
        assert.notEqual(n, 5); // not the -Z face behind it
    }
});

test("neighbourFaceInDirection follows a rotated camera basis", () => {
    // Camera rolled 90° about the view axis: screen-right = +Y, screen-up = -X.
    const R: [number, number, number] = [0, 1, 0];
    const U: [number, number, number] = [-1, 0, 0];
    // On the +Z face, ArrowRight should now pick +Y (projects most to the right).
    assert.equal(neighbourFaceInDirection(4, "right", R, U), 2); // +Y
    assert.equal(neighbourFaceInDirection(4, "up", R, U), 1); // -X projects up
});

test("neighbourFaceInDirection maps the +X face's in-plane neighbours (Y up/down)", () => {
    const R: [number, number, number] = [1, 0, 0];
    const U: [number, number, number] = [0, 1, 0];
    // +X face (0): in-plane axes Y,Z. Y projects to screen-up; Z is edge-on.
    assert.equal(neighbourFaceInDirection(0, "up", R, U), 2); // +Y
    assert.equal(neighbourFaceInDirection(0, "down", R, U), 3); // -Y
});

test("boundsToBox spans min→max as origin+size", () => {
    const box = boundsToBox([1, 2, 3], [4, 6, 9]);
    assert.deepEqual(box.origin, [1, 2, 3]);
    assert.deepEqual(box.size, [3, 4, 6]);
});

test("boundsToBox clamps an inverted span to zero size", () => {
    const box = boundsToBox([0, 0, 0], [-1, 5, 5]);
    assert.deepEqual(box.size, [0, 5, 5]);
});

test("cadDisplayBox fits the CAD bounds when present, ignoring the declared box", () => {
    const cell = {origin: [10, 10, 0] as [number, number, number], size: [2, 2, 2] as [number, number, number]};
    // The CAD extends further than the declared 2×2×2 box and shares the
    // min corner (the compiler seats the CAD min corner on the cell origin).
    const box = cadDisplayBox(cell, {min: [10, 10, 0], max: [13, 11.5, 5]});
    assert.deepEqual(box.origin, [10, 10, 0]);
    assert.deepEqual(box.size, [3, 1.5, 5]);
});

test("cadDisplayBox falls back to a copy of the declared box with no CAD", () => {
    const cell = {origin: [1, 2, 3] as [number, number, number], size: [4, 5, 6] as [number, number, number]};
    const box = cadDisplayBox(cell, null);
    assert.deepEqual(box.origin, [1, 2, 3]);
    assert.deepEqual(box.size, [4, 5, 6]);
    // A copy — mutating the result must not touch the cell.
    box.origin[0] = 99;
    assert.equal(cell.origin[0], 1);
});
