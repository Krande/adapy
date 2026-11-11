// The placement, camera and grid rules both viewports share (viewport/api): what three.js did inline
// before they were shared, number for number.
import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import {placeModel, recentreTranslation} from "../../viewport/api/sceneFrame";
import {boundingSphere, clipRangeFor, DEFAULT_VIEW, framePose, sphereFitDistance, wholeSphereDistance} from "../../viewport/api/cameraFrame";
import {GROUND_GRID, groundGridLines} from "../../viewport/api/groundGrid";

const box = [100, -20, 10, 300, 20, 50] as const;
/** -0 as 0: a centre at 0 negated is -0, in three.js' Vector3 as here. */
const plain = (v: number[] | null) => v && v.map((x) => x + 0);

test("a model is recentred: centre over the origin, bottom 5% of its height above the ground", () => {
    assert.deepEqual(plain(recentreTranslation(box, true)), [-200, 0, -10 + 2]);
    assert.deepEqual(plain(recentreTranslation(box, false)), [-200, 20 + 2, -30]);
});

test("placement reuses the scene's translation for a model that joins, and none when locked", () => {
    const opts = {zIsUp: true, locked: false, current: null, reuseCurrent: false};
    assert.deepEqual(plain(placeModel(box, opts).translation), [-200, 0, -8]);
    assert.equal(placeModel(box, opts).isNew, true);
    assert.deepEqual(placeModel(box, {...opts, current: [1, 2, 3], reuseCurrent: true}), {translation: [1, 2, 3], isNew: false});
    assert.deepEqual(plain(placeModel(box, {...opts, current: [1, 2, 3], reuseCurrent: false}).translation), [-200, 0, -8]);
    assert.deepEqual(placeModel(box, {...opts, locked: true}), {translation: null, isNew: false});
    assert.deepEqual(placeModel(box, {...opts, locked: true, current: [1, 2, 3], reuseCurrent: true}), {translation: [1, 2, 3], isNew: false});
});

test("the bounding sphere is three.js' Box3.getBoundingSphere", () => {
    const b = new THREE.Box3(new THREE.Vector3(box[0], box[1], box[2]), new THREE.Vector3(box[3], box[4], box[5]));
    const want = b.getBoundingSphere(new THREE.Sphere());
    const got = boundingSphere(box);
    assert.deepEqual(got.center, want.center.toArray());
    assert.ok(Math.abs(got.radius - want.radius) < 1e-9);
});

test("the fit puts the camera the sphere-fit distance back along the view direction", () => {
    const dir = new THREE.Vector3(1, -1, -1).normalize().toArray();
    const pose = framePose(box, dir, 60, 1.5)!;
    const d = sphereFitDistance(pose.radius, 60, 1.5);
    const back = Math.hypot(pose.eye[0] - pose.target[0], pose.eye[1] - pose.target[1], pose.eye[2] - pose.target[2]);
    assert.ok(Math.abs(back - d) < 1e-9);
    assert.ok(wholeSphereDistance(pose.radius, 60, 1.5) > d);
    assert.equal(framePose([1, 1, 1, 0, 0, 0], dir, 60, 1), null, "an empty box");
    assert.equal(framePose([1, 1, 1, 1, 1, 1], dir, 60, 1, 1, 1e-4), null, "a box of no size");
});

test("clip range: near 1e-3 and far 1e3 of the radius, with floors", () => {
    assert.deepEqual(clipRangeFor(100), {near: 0.1, far: 100000});
    assert.deepEqual(clipRangeFor(1e-6), {near: 1e-6, far: 1e-2});
    assert.equal(clipRangeFor(0), null);
});

test("the empty-scene view is the one three.js always started from", () => {
    assert.deepEqual(DEFAULT_VIEW, {eye: [-5, 5, 5], target: [0, 0, 0], fovDeg: 60, near: 0.1, far: 10000});
});

test("the grid's lines are three.js' GridHelper's, laid on XY in a Z-up world", () => {
    const helper = new THREE.GridHelper(GROUND_GRID.size, GROUND_GRID.divisions, GROUND_GRID.centreColor, GROUND_GRID.lineColor);
    helper.rotation.x = Math.PI / 2;
    helper.updateMatrixWorld(true);
    const pos = helper.geometry.getAttribute("position");
    const key = (x: number, y: number, z: number) => [x, y, z].map((v) => Math.round(v * 1e6) / 1e6 + 0).join(",");
    const want = new Set<string>();
    const v = new THREE.Vector3();
    for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(helper.matrixWorld);
        want.add(key(v.x, v.y, v.z));
    }
    const {lines, centre} = groundGridLines(true);
    const got = new Set<string>();
    for (const arr of [lines, centre]) for (let i = 0; i < arr.length; i += 3) got.add(key(arr[i], arr[i + 1], arr[i + 2]));
    assert.deepEqual([...got].sort(), [...want].sort());
    assert.equal(centre.length / 6, 2, "two lines through the origin");
    assert.equal((lines.length + centre.length) / 6, 2 * (GROUND_GRID.divisions + 1));
});
