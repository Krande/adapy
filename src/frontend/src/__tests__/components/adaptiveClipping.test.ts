// A compile/recompile result loads with autoFitOverride === false so the camera does not move.
// applyAdaptiveClipping still has to adapt near/far to the new model's size (a tiny
// result must stay zoomable), but on that path it must only WIDEN the range: a smaller near plane or
// a smaller controls.minDistance only ever lets the camera get CLOSER (never moves it), but a smaller
// controls.maxDistance (or far) can be BELOW where the camera already sits, so the next controls
// update clamps the camera back in — which looks exactly like the camera moved even though no fit
// call ran. So widenOnly takes the min of near/minDistance and the max of far/maxDistance, never the
// new value outright.
import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls.js";
import {applyAdaptiveClipping} from "../../components/viewer/sceneHelpers/adaptiveClipping";

function makeCameraAndControls(near: number, far: number, minDistance: number, maxDistance: number) {
    const camera = new THREE.PerspectiveCamera(60, 1, near, far);
    const controls = new OrbitControls(camera, null);
    controls.minDistance = minDistance;
    controls.maxDistance = maxDistance;
    return {camera, controls};
}

test("a normal load sets the range to the new model's radius, even if that shrinks it", () => {
    const {camera, controls} = makeCameraAndControls(100, 100000, 100, 100000);
    applyAdaptiveClipping(camera, controls, 1); // a much smaller model
    assert.ok(camera.near < 100, "near shrinks for a smaller model");
    assert.ok(camera.far < 100000, "far shrinks for a smaller model");
    assert.ok(controls.minDistance < 100);
    assert.ok(controls.maxDistance < 100000);
});

test("widenOnly never pulls the camera in for a smaller result", () => {
    const {camera, controls} = makeCameraAndControls(100, 100000, 100, 100000);
    applyAdaptiveClipping(camera, controls, 1, {widenOnly: true}); // a much smaller model
    // near/minDistance may shrink (that only widens how close the camera CAN get, never moves it);
    // far/maxDistance is the dangerous direction and must not shrink below where it already was.
    assert.ok(camera.near <= 100, "near may shrink; it only permits getting closer");
    assert.equal(camera.far, 100000, "far is not pulled in — the camera's own far limit stays");
    assert.ok(controls.minDistance <= 100);
    assert.equal(controls.maxDistance, 100000, "the dolly-out limit is not pulled in");
});

test("widenOnly still widens the range for a bigger result", () => {
    const {camera, controls} = makeCameraAndControls(0.1, 100, 0.1, 100);
    applyAdaptiveClipping(camera, controls, 10000, {widenOnly: true}); // a much bigger model
    assert.ok(camera.far > 100, "far widens to cover the bigger model");
    assert.ok(controls.maxDistance > 100, "the dolly-out limit widens too");
    // near only ever shrinks the window further in normally; widenOnly keeps the wider (smaller) one.
    assert.ok(camera.near <= 0.1);
});

test("without opts, behaviour is unchanged (the default is the old, always-replace rule)", () => {
    const {camera, controls} = makeCameraAndControls(100, 100000, 100, 100000);
    applyAdaptiveClipping(camera, controls, 1);
    assert.notEqual(camera.near, 100);
    assert.notEqual(controls.maxDistance, 100000);
});
