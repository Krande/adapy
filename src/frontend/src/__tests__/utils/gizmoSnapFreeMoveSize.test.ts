// The builder's move / resize gizmos, three things a user hit:
//  - an opening moved along X with snapping on also had its Z snapped to the grid: only the axes
//    being dragged may move (and snap); the others keep their values exactly;
//  - holding the left Shift key moves freely (no grid step) for as long as it is held;
//  - the gizmo's arrows changed size as the view orbited and zoomed, tiny at times, drawn
//    orthographically: it is now one size on screen there and in perspective, and so are the
//    resize handles.

import assert from "node:assert/strict";
import {test} from "node:test";
import * as THREE from "three";

import {axesOfHandle, movedOrigin} from "@/utils/cellbuilder/snap";
import {freeMove, installFreeMoveKeys, onFreeMoveChange, setFreeMove, snapStep} from "@/utils/gizmo/freeMove";
import {gizmoSizeFor, screenSizedScale, transformControlsFactor} from "@/utils/gizmo/screenSize";
import {updateProjection, viewHeightAt} from "@/utils/scene/orthographicProjection";

test("a move along X leaves Y and Z exactly as they were, off the grid or not", () => {
    const origin: [number, number, number] = [1.03, 2.07, 3.14159];
    const size: [number, number, number] = [0.9, 0.2, 2.1];
    // The arrow dragged the centre to x = 2.04 (and three.js left y, z a hair off: float noise).
    const center: [number, number, number] = [2.04, 2.07 + 0.1 + 1e-12, 3.14159 + 1.05 - 1e-12];
    const out = movedOrigin(origin, size, center, axesOfHandle("X"), 0.1);
    assert.deepEqual(out, [1.6, 2.07, 3.14159]);
});

test("a plane handle moves (and snaps) its two axes; the centre handle all three", () => {
    const origin: [number, number, number] = [0.03, 0.07, 0.11];
    const size: [number, number, number] = [1, 1, 1];
    const center: [number, number, number] = [2.04, 3.06, 4.08];
    assert.deepEqual(movedOrigin(origin, size, center, axesOfHandle("XY"), 0.1), [1.5, 2.6, 0.11]);
    assert.deepEqual(movedOrigin(origin, size, center, axesOfHandle("XYZ"), 0.1), [1.5, 2.6, 3.6]);
    assert.deepEqual(axesOfHandle(null), [0, 1, 2]);
    assert.deepEqual(axesOfHandle("YZ"), [1, 2]);
});

test("free move: no step while the left Shift key is held, the step again once let go", () => {
    setFreeMove(false);
    let changes = 0;
    const off = onFreeMoveChange(() => changes++);
    assert.equal(snapStep(0.1), 0.1);
    setFreeMove(true);
    assert.equal(freeMove(), true);
    assert.equal(snapStep(0.1), 0);
    const origin: [number, number, number] = [0, 0, 0];
    assert.deepEqual(movedOrigin(origin, [1, 1, 1], [1.234, 0.5, 0.5], [0], snapStep(0.1)), [0.734, 0, 0]);
    setFreeMove(false);
    assert.equal(changes, 2);
    off();
});

test("only the LEFT Shift key frees the move, and a lost focus ends it", () => {
    // A window stand-in (Node's EventTarget does not match a removal by a bare `true`, a browser does).
    const heard = new Map<string, Set<(e: {code?: string}) => void>>();
    const target = {
        addEventListener: (type: string, l: (e: {code?: string}) => void, capture: boolean) => {
            assert.equal(capture, true, "the capture phase");
            heard.set(type, (heard.get(type) ?? new Set()).add(l));
        },
        removeEventListener: (type: string, l: (e: {code?: string}) => void) => void heard.get(type)?.delete(l),
    };
    const release = installFreeMoveKeys(target);
    const key = (type: string, code: string) => {
        for (const l of heard.get(type) ?? []) l({code});
    };
    key("keydown", "ShiftRight");
    assert.equal(freeMove(), false, "the right Shift does not");
    key("keydown", "ShiftLeft");
    assert.equal(freeMove(), true);
    key("keyup", "ShiftLeft");
    assert.equal(freeMove(), false);
    key("keydown", "ShiftLeft");
    key("blur", "");
    assert.equal(freeMove(), false, "a key let go outside the window is not left held");
    release();
    key("keydown", "ShiftLeft");
    assert.equal(freeMove(), false, "released: no longer listening");
});

/** The share of the view's height the gizmo's handles cover, with the size we give it. */
const gizmoShare = (camera: THREE.Camera, at: THREE.Vector3): number =>
    (transformControlsFactor(camera, at) * gizmoSizeFor(camera, at)) / viewHeightAt(camera, at);

const perspective = () => {
    const camera = new THREE.PerspectiveCamera(45, 1.5, 0.1, 1e5);
    camera.position.set(0, -40, 10);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld(true);
    return camera;
};

test("in perspective the gizmo covers one share of the view wherever it is", () => {
    const camera = perspective();
    const near = gizmoShare(camera, new THREE.Vector3(0, -35, 9));
    const far = gizmoShare(camera, new THREE.Vector3(5, 60, -3));
    assert.ok(Math.abs(near - far) < 1e-9, `${near} vs ${far}`);
    // Its size there is three.js's own (1): nothing changes in a plain perspective view.
    assert.ok(Math.abs(gizmoSizeFor(camera, new THREE.Vector3(0, 0, 0)) - 1) < 1e-9);
});

test("drawn orthographically the gizmo is one size on screen, whatever its depth and the zoom", () => {
    const camera = perspective();
    // The orthographic view, as tall as the perspective one at the target 41.2 m away.
    updateProjection(camera, true, camera.position.length());
    const shares = [
        new THREE.Vector3(0, -38, 9.5), // just in front of the eye: three.js drew it tiny
        new THREE.Vector3(0, 0, 0), // at the target
        new THREE.Vector3(0, 200, -40), // far behind the target: three.js drew it huge
    ].map((p) => gizmoShare(camera, p));
    for (const s of shares) assert.ok(Math.abs(s - shares[1]) < 1e-9, shares.join(", "));
    // And the same share as in perspective.
    assert.ok(Math.abs(shares[1] - gizmoShare(perspective(), new THREE.Vector3())) < 1e-9);
    // A zoom (the distance to the target) changes the view's height, not the gizmo's share of it.
    updateProjection(camera, true, 5);
    assert.ok(Math.abs(gizmoShare(camera, new THREE.Vector3(0, 200, -40)) - shares[1]) < 1e-9);
    updateProjection(camera, false, 0);
});

test("a resize handle covers the same share of the view at any distance and in either projection", () => {
    const camera = perspective();
    const share = 0.011;
    const at = [new THREE.Vector3(0, -30, 8), new THREE.Vector3(0, 80, 0)];
    for (const p of at) assert.ok(Math.abs(screenSizedScale(camera, p, share) / viewHeightAt(camera, p) - share) < 1e-12);
    // Far away it is larger in model units, so it looks the same.
    assert.ok(screenSizedScale(camera, at[1], share) > screenSizedScale(camera, at[0], share));
    updateProjection(camera, true, camera.position.length());
    const a = screenSizedScale(camera, at[0], share);
    const b = screenSizedScale(camera, at[1], share);
    assert.ok(Math.abs(a - b) < 1e-12, "orthographic: one height everywhere, so one radius");
    updateProjection(camera, false, 0);
});
