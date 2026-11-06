// Named camera views: top, bottom, front, back, left, right and isometric (View -> Camera).
//
// A view looks at the model from one side, framing everything "fit all" frames, with the same
// FOV fit and adaptive clipping (`./cameraFit`) and, on camera-controls, the same smooth
// transition. The directions are in the viewer's world: Z up by default (`zIsUp`, what
// `setupCamera` and `ThreeCanvas` set), else Y up. "Front" looks along +Y in a Z-up world
// (from -Y), the usual drawing convention; the Y-up directions are the same views turned the way
// a Y-up glTF is turned into this viewer's Z-up world ((x, y, z) -> (x, z, -y)).
import * as THREE from "three";
import CameraControls from "camera-controls";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";
import {fitDistance, sceneFitBox} from "@/components/viewer/sceneHelpers/cameraFit";

import {CAMERA_VIEWS, type CameraView} from "@/layout/coreCommandIds";

export const NAMED_VIEWS = CAMERA_VIEWS;
export type NamedView = CameraView;

/** Looking straight down or up, the up axis is parallel to the view and the picture's roll is
 *  undefined: the camera is set this far off the axis, toward the front for top (so +Y, the back,
 *  is up on the screen) and toward the back for bottom. */
const POLE_NUDGE = 1e-3;

const ISO = 1 / Math.sqrt(3);

/** How much farther than the tight fit a view stands. */
const VIEW_MARGIN = 1.1;

/** Where the camera stands from the target, per view, in a Z-up world (unit length). */
const Z_UP: Readonly<Record<NamedView, readonly [number, number, number]>> = {
    top: [0, -POLE_NUDGE, 1],
    bottom: [0, POLE_NUDGE, -1],
    front: [0, -1, 0],
    back: [0, 1, 0],
    left: [-1, 0, 0],
    right: [1, 0, 0],
    // From the front, right and above.
    isometric: [ISO, -ISO, ISO],
};

/** The unit vector from the target to the camera for `view`, in a Z-up (default) or Y-up world. */
export function namedViewDirection(view: NamedView, zIsUp = true): [number, number, number] {
    const [x, y, z] = Z_UP[view];
    const v: [number, number, number] = zIsUp ? [x, y, z] : [x, z, -y];
    const n = Math.hypot(v[0], v[1], v[2]);
    return [v[0] / n, v[1] / n, v[2] / n];
}

/** Point the camera at the model from `view`'s side and frame it. With nothing loaded the camera
 *  turns about its current target at its current distance. */
export function applyNamedView(
    view: NamedView,
    scene: THREE.Object3D,
    camera: THREE.PerspectiveCamera,
    controls: OrbitControls | CameraControls,
    zIsUp = true,
): void {
    const dir = new THREE.Vector3(...namedViewDirection(view, zIsUp));
    const box = sceneFitBox(scene);
    let center: THREE.Vector3;
    let distance: number;
    if (box) {
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        const radius = Math.max(sphere.radius, 1e-4);
        center = sphere.center.clone();
        applyAdaptiveClipping(camera, controls, radius);
        // A little room around it: the sphere's outline is the model's at its corners only.
        distance = fitDistance(camera, radius) * VIEW_MARGIN;
    } else {
        center = controls instanceof CameraControls ? controls.getTarget(new THREE.Vector3()) : controls.target.clone();
        distance = Math.max(camera.position.distanceTo(center), 1e-3);
    }
    const pos = center.clone().addScaledVector(dir, distance);
    if (controls instanceof CameraControls) {
        void controls.setLookAt(pos.x, pos.y, pos.z, center.x, center.y, center.z, true);
        camera.updateProjectionMatrix();
    } else {
        camera.position.copy(pos);
        camera.lookAt(center);
        controls.target.copy(center);
        camera.updateProjectionMatrix();
        controls.update();
    }
}
