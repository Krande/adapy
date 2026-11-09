// The WebGPU embed's camera: the preset framing puts the model's centre mid-view with its bounding
// sphere inside the view, and orbiting keeps the target there.
import {test} from "node:test";
import assert from "node:assert/strict";

import {OrbitCamera} from "../../../embed-webgpu/camera";

function project(m: {view: Float32Array; proj: Float32Array}, p: [number, number, number]): [number, number] {
    const mul = (a: Float32Array, v: number[]) => [0, 1, 2, 3].map((r) => a[r] * v[0] + a[4 + r] * v[1] + a[8 + r] * v[2] + a[12 + r] * v[3]);
    const c = mul(m.proj, mul(m.view, [...p, 1]));
    return [c[0] / c[3], c[1] / c[3]];
}

test("the preset framing centres the model and fits its bounding sphere", () => {
    const cam = new OrbitCamera();
    cam.frame([0, -0.1, -0.2, 3, 0.1, 0.2], {name: "iso", azimuth_deg: -135, elevation_deg: 30}, 4 / 3);
    const m = cam.matrices(4 / 3);
    const [x, y] = project(m, [1.5, 0, 0]);
    assert.ok(Math.abs(x) < 1e-5 && Math.abs(y) < 1e-5);
    for (const corner of [[0, -0.1, -0.2], [3, 0.1, 0.2], [0, 0.1, 0.2], [3, -0.1, -0.2]] as [number, number, number][]) {
        const [cx, cy] = project(m, corner);
        assert.ok(Math.abs(cx) < 1 && Math.abs(cy) < 1, `corner ${corner} is in view`);
    }
});

test("orbiting keeps the target in the middle of the view", () => {
    const cam = new OrbitCamera();
    cam.frame([0, 0, 0, 1, 1, 1], undefined, 1);
    cam.orbit(120, -40, 480);
    const [x, y] = project(cam.matrices(1), [0.5, 0.5, 0.5]);
    assert.ok(Math.abs(x) < 1e-5 && Math.abs(y) < 1e-5);
});

test("dragging right moves the model's near side right", () => {
    const cam = new OrbitCamera();
    cam.frame([-1, -1, -1, 1, 1, 1], undefined, 1);
    const eye = cam.eye();
    const len = Math.hypot(eye[0], eye[1]);
    const near: [number, number, number] = [eye[0] / len, eye[1] / len, 0]; // towards the camera
    const before = project(cam.matrices(1), near)[0];
    cam.orbit(20, 0, 500);
    assert.ok(project(cam.matrices(1), near)[0] > before);
});
