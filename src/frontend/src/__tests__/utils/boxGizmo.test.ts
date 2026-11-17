// A placed box's maths (utils/gizmo/boxPose.ts) and the transform gizmo's key rules
// (utils/gizmo/transformKeys.ts), which the cell builder's key handler and a plugin's placed box share.
import assert from "node:assert/strict";
import {test} from "node:test";

import {
    BOX_FACE_INDICES,
    alongAxis,
    arrowSegments,
    boxCorners,
    boxEdgeSegments,
    distanceToSegment,
    guideSegment,
    handleReach,
    inPlane,
    insidePolygon,
    loopSegments,
    planeSquareCorners,
    poseToScene,
    ringPoints,
    snapTo,
    turnBetween,
    type LocalBox,
} from "@/utils/gizmo/boxPose";
import {
    GIZMO_FALLBACK_STEP,
    GIZMO_ROTATE_SNAP_DEG,
    gizmoAxisOfKey,
    gizmoModeOfKey,
    gizmoMoveStep,
    isTextEntry,
    toggledAxisLock,
} from "@/utils/gizmo/transformKeys";

const near = (a: ArrayLike<number>, b: ArrayLike<number>, eps = 1e-6) => {
    assert.equal(a.length, b.length);
    for (let i = 0; i < a.length; i++) assert.ok(Math.abs(a[i] - b[i]) <= eps, `[${i}] ${a[i]} is not ${b[i]}`);
};

const BOX: LocalBox = [-1, -0.5, 0, 1, 0.5, 2];

test("a pose places the box's own frame: moved by its position, turned about Z", () => {
    near(poseToScene({position: [10, 20, 3], yawDeg: 0}, [1, 0.5, 2]), [11, 20.5, 5]);
    // A quarter turn anticlockwise seen from above: the box's +x points along the scene's +y.
    near(poseToScene({position: [10, 20, 3], yawDeg: 90}, [1, 0, 0]), [10, 21, 3]);
    near(poseToScene({position: [0, 0, 0], yawDeg: 90}, [0, 1, 0]), [-1, 0, 0]);
});

test("the box's corners, edges and faces", () => {
    const corners = boxCorners(BOX, {position: [0, 0, 0], yawDeg: 0});
    near(corners.subarray(0, 3), [-1, -0.5, 0]);
    near(corners.subarray(21, 24), [1, 0.5, 2]);
    const edges = boxEdgeSegments(BOX, {position: [0, 0, 0], yawDeg: 0});
    assert.equal(edges.length, 12 * 6);
    // Four edges each along x (2 long), y (1) and z (2): 20 in all.
    let total = 0;
    for (let i = 0; i < edges.length; i += 6) total += Math.hypot(edges[i + 3] - edges[i], edges[i + 4] - edges[i + 1], edges[i + 5] - edges[i + 2]);
    near([total], [20]);
    assert.equal(BOX_FACE_INDICES.length, 36);
    assert.ok(BOX_FACE_INDICES.every((i) => i < 8));
    // Turned and moved, the box is the same size.
    const turned = boxEdgeSegments(BOX, {position: [5, 5, 5], yawDeg: 37});
    let again = 0;
    for (let i = 0; i < turned.length; i += 6) again += Math.hypot(turned[i + 3] - turned[i], turned[i + 4] - turned[i + 1], turned[i + 5] - turned[i + 2]);
    near([again], [20], 1e-4);
});

test("the handles are in proportion to the box and stand at its origin", () => {
    const reach = handleReach(BOX);
    near([reach], [1.5]);
    const arrow = arrowSegments([1, 2, 3], 0, reach);
    assert.equal(arrow.length, 18);
    near(arrow.subarray(0, 6), [1, 2, 3, 2.5, 2, 3]);
    const up = arrowSegments([0, 0, 0], 2, 2);
    near(up.subarray(3, 6), [0, 0, 2]);
    const square = planeSquareCorners([1, 2, 3], 2);
    near(square.flat(), [1.5, 2.5, 3, 2, 2.5, 3, 2, 3, 3, 1.5, 3, 3]);
    assert.equal(loopSegments(square).length, 24);
    const ring = ringPoints([1, 2, 3], 2);
    assert.ok(ring.every((p) => Math.abs(Math.hypot(p[0] - 1, p[1] - 2) - 2) < 1e-9 && p[2] === 3));
    near(guideSegment([1, 2, 3], 1, 10), [1, -8, 3, 1, 12, 3]);
});

test("the pointer on screen as a move along an axis", () => {
    // An axis 2 long drawn from (100, 100) to (300, 100): 100 px a unit.
    const base = {x: 100, y: 100}, tip = {x: 300, y: 100};
    assert.equal(alongAxis({x: 150, y: 400}, base, tip, 2), 0.5);
    assert.equal(alongAxis({x: 0, y: 100}, base, tip, 2), -1);
    // Seen end-on, an axis has no direction to follow.
    assert.equal(alongAxis({x: 150, y: 400}, base, {x: 100.2, y: 100}, 2), null);
});

test("the pointer on screen as a point of the floor's plane", () => {
    // Seen from above, y up the screen: 100 px a unit.
    const o = {x: 400, y: 300}, ex = {x: 500, y: 300}, ey = {x: 400, y: 200};
    near(inPlane({x: 450, y: 250}, o, ex, ey, 1)!, [0.5, 0.5]);
    near(inPlane({x: 300, y: 500}, o, ex, ey, 1)!, [-1, -2]);
    // A view turned and sheared is still one plane.
    near(inPlane({x: 460, y: 330}, o, {x: 430, y: 340}, {x: 430, y: 290}, 2)!, [2, 2]);
    assert.equal(inPlane({x: 450, y: 250}, o, ex, {x: 300, y: 300}, 1), null); // edge-on
});

test("what a press is on", () => {
    assert.equal(distanceToSegment({x: 5, y: 3}, {x: 0, y: 0}, {x: 10, y: 0}), 3);
    assert.equal(distanceToSegment({x: 14, y: 3}, {x: 0, y: 0}, {x: 10, y: 0}), 5);
    const square = [{x: 0, y: 0}, {x: 10, y: 0}, {x: 10, y: 10}, {x: 0, y: 10}];
    assert.equal(insidePolygon({x: 5, y: 5}, square), true);
    assert.equal(insidePolygon({x: 15, y: 5}, square), false);
    assert.equal(insidePolygon({x: 5, y: 5}, [...square].reverse()), true);
});

test("a drag snaps to a step, and a turn goes the short way round", () => {
    assert.equal(snapTo(1.234, 0.1), 1.2);
    assert.equal(snapTo(0.25 + 0.05, 0.1), 0.3);
    assert.equal(snapTo(-0.04, 0.1), 0);
    assert.equal(snapTo(1.234, 0), 1.234);
    assert.equal(snapTo(22, 15), 15);
    assert.equal(snapTo(23, 15), 30);
    assert.equal(turnBetween(170, -170), 20);
    assert.equal(turnBetween(-170, 170), -20);
    assert.equal(turnBetween(0, 270), -90);
    assert.equal(turnBetween(0, 180), 180);
});

test("the gizmo's keys: G, R and S bring one up, X, Y and Z lock it and release it", () => {
    assert.deepEqual(["g", "r", "s", "x", "G"].map(gizmoModeOfKey), ["translate", "rotate", "resize", null, null]);
    assert.deepEqual(["x", "y", "z", "g"].map(gizmoAxisOfKey), [0, 1, 2, null]);
    assert.equal(toggledAxisLock(null, 0), 0);
    assert.equal(toggledAxisLock(0, 0), null);
    assert.equal(toggledAxisLock(0, 2), 2);
});

test("the gizmo's steps, and whose a key is", () => {
    assert.equal(GIZMO_ROTATE_SNAP_DEG, 15);
    assert.equal(gizmoMoveStep(0.5), 0.5);
    assert.equal(gizmoMoveStep(0), GIZMO_FALLBACK_STEP);
    assert.equal(gizmoMoveStep(undefined), 0.1);
    assert.equal(isTextEntry({tagName: "INPUT"}), true);
    assert.equal(isTextEntry({tagName: "SELECT"}), true);
    assert.equal(isTextEntry({tagName: "BUTTON"}), false);
    assert.equal(isTextEntry(null), false);
});
