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
import {namedViewDirection} from "@/utils/camera/namedViewDirection";

export {namedViewDirection};

export const NAMED_VIEWS = CAMERA_VIEWS;
export type NamedView = CameraView;

/** How much farther than the tight fit a view stands. */
const VIEW_MARGIN = 1.1;

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
