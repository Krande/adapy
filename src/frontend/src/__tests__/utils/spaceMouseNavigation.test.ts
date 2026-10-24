// Object-mode 3D-mouse navigation on a bare position/target pose: the model
// follows the cap, so the camera does the opposite about the orbit target.

import assert from "node:assert/strict";
import { test } from "node:test";
import { Vector3 } from "three";

import { applyObjectModeMotion } from "../../utils/spacemouse/navigation";

const UP = new Vector3(0, 1, 0);
const RATES = { rotate: 1, pan: 1, zoom: 1 };

/** Camera 10 units out on +z, looking at the origin. */
function pose() {
    return { position: new Vector3(0, 0, 10), target: new Vector3(0, 0, 0) };
}

function near(a: number, b: number, eps = 1e-6) {
    assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
}

test("rest leaves the pose alone", () => {
    const p = pose();
    assert.equal(applyObjectModeMotion(p.position, p.target, UP, [0, 0, 0, 0, 0, 0], 0.016, 50, 1, RATES), false);
    assert.deepEqual(p.position.toArray(), [0, 0, 10]);
});

test("pushing right moves the model right: camera and target both go left, by the same amount", () => {
    const p = pose();
    applyObjectModeMotion(p.position, p.target, UP, [1, 0, 0, 0, 0, 0], 0.1, 90, 1, RATES);
    // half-height at distance 10 with a 90 deg fov is 10; 1 half-height/s for 0.1 s
    near(p.position.x, -1);
    near(p.target.x, -1);
    near(p.position.distanceTo(p.target), 10);
});

test("lifting the cap moves the model up: the camera goes down", () => {
    const p = pose();
    applyObjectModeMotion(p.position, p.target, UP, [0, 1, 0, 0, 0, 0], 0.1, 90, 1, RATES);
    assert.ok(p.position.y < 0 && p.target.y < 0);
    near(p.position.y, p.target.y);
});

test("pulling toward you brings the model closer; pushing away backs off; the target stays put", () => {
    const p = pose();
    applyObjectModeMotion(p.position, p.target, UP, [0, 0, 1, 0, 0, 0], 1, 50, 1, RATES);
    near(p.position.z, 10 * Math.exp(-1));
    assert.deepEqual(p.target.toArray(), [0, 0, 0]);
    const q = pose();
    applyObjectModeMotion(q.position, q.target, UP, [0, 0, -1, 0, 0, 0], 1, 50, 1, RATES);
    near(q.position.z, 10 * Math.E);
});

test("zoom never reaches the target however long it runs", () => {
    const p = pose();
    for (let i = 0; i < 1000; i++) applyObjectModeMotion(p.position, p.target, UP, [0, 0, 1, 0, 0, 0], 0.05, 50, 3, RATES);
    assert.ok(p.position.distanceTo(p.target) > 0);
    assert.ok(p.position.z > 0, "still on the near side");
});

test("twist turns the model with the cap: the camera circles the target the other way, at constant distance", () => {
    const p = pose();
    // +ry: cap turned counter-clockwise seen from above -> camera clockwise from above
    applyObjectModeMotion(p.position, p.target, UP, [0, 0, 0, 0, 1, 0], Math.PI / 2, 50, 1, RATES);
    // Clockwise from above (looking down -y) takes +z to -x.
    near(p.position.x, -10);
    near(p.position.z, 0);
    near(p.position.y, 0);
    assert.deepEqual(p.target.toArray(), [0, 0, 0]);
});

test("tilt pitches about the target and stops short of the pole", () => {
    const p = pose();
    // +rx: the model's top tilts toward you, so the camera rises over it.
    applyObjectModeMotion(p.position, p.target, UP, [0, 0, 0, 1, 0, 0], 0.5, 50, 1, RATES);
    assert.ok(p.position.y > 0);
    near(p.position.length(), 10);
    // Hold it far past vertical: the camera must stop just short of straight over.
    for (let i = 0; i < 100; i++) applyObjectModeMotion(p.position, p.target, UP, [0, 0, 0, 1, 0, 0], 0.05, 50, 1, RATES);
    const dir = p.target.clone().sub(p.position).normalize();
    assert.ok(dir.dot(UP) > -1 && dir.dot(UP) < -0.999, "looking almost straight down, not flipped");
    near(p.position.length(), 10);
});

test("a rotation about a moved target orbits that target, not the origin", () => {
    const p = { position: new Vector3(5, 0, 10), target: new Vector3(5, 0, 0) };
    applyObjectModeMotion(p.position, p.target, UP, [0, 0, 0, 0, 1, 0], Math.PI, 50, 1, RATES);
    near(p.position.x, 5);
    near(p.position.z, -10);
    assert.deepEqual(p.target.toArray(), [5, 0, 0]);
});

test("Z-up worlds turn about Z", () => {
    const zUp = new Vector3(0, 0, 1);
    const p = { position: new Vector3(10, 0, 0), target: new Vector3(0, 0, 0) };
    applyObjectModeMotion(p.position, p.target, zUp, [0, 0, 0, 0, 1, 0], Math.PI / 2, 50, 1, RATES);
    near(p.position.z, 0);
    near(Math.hypot(p.position.x, p.position.y), 10);
});

test("roll has no effect: the view stays level", () => {
    const p = pose();
    assert.equal(applyObjectModeMotion(p.position, p.target, UP, [0, 0, 0, 0, 0, 1], 0.5, 50, 1, RATES), false);
    assert.deepEqual(p.position.toArray(), [0, 0, 10]);
});

test("speed is frame-rate independent: one long frame equals many short ones", () => {
    const a = pose();
    const b = pose();
    applyObjectModeMotion(a.position, a.target, UP, [0.5, 0, 0.3, 0, 0, 0], 0.04, 50, 1, RATES);
    for (let i = 0; i < 4; i++) applyObjectModeMotion(b.position, b.target, UP, [0.5, 0, 0.3, 0, 0, 0], 0.01, 50, 1, RATES);
    // Pan scales with distance, which zoom changes in between, so not exact; close.
    near(a.position.z, b.position.z, 1e-3);
    near(a.position.x, b.position.x, 2e-2);
});

test("gain scales the motion", () => {
    const a = pose();
    const b = pose();
    applyObjectModeMotion(a.position, a.target, UP, [0, 0, 0, 0, 0.5, 0], 0.1, 50, 1, RATES);
    applyObjectModeMotion(b.position, b.target, UP, [0, 0, 0, 0, 0.5, 0], 0.1, 50, 2, RATES);
    const angA = Math.atan2(-a.position.x, a.position.z);
    const angB = Math.atan2(-b.position.x, b.position.z);
    near(angB, 2 * angA);
});

test("per-frame step is cheap", () => {
    const p = pose();
    const axes = new Float32Array([0.1, -0.05, 0.02, 0.2, -0.3, 0]);
    const N = 100_000;
    const t0 = performance.now();
    for (let i = 0; i < N; i++) applyObjectModeMotion(p.position, p.target, UP, axes, 0.016, 50, 1);
    const us = ((performance.now() - t0) * 1000) / N;
    assert.ok(us < 20, `${us.toFixed(3)} us per frame`);
    console.log(`spacemouse camera step: ${us.toFixed(4)} us/frame over ${N} frames`);
});
