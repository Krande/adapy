// Moving the camera from the keyboard: one step of orbit or pan per key press, on whichever three.js
// controller is in use. The keys themselves are core shortcuts (layout/coreShortcutTable), so they are
// listed and rebound in the Shortcuts dialog like every other key.
import * as THREE from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import CameraControls from "camera-controls";

/** One key press orbits by this much. */
export const ORBIT_STEP_RAD = (15 * Math.PI) / 180;
/** One key press pans by this fraction of the distance to what the camera looks at. */
export const PAN_STEP_FRACTION = 0.1;
/** Stay off the poles: OrbitControls' camera flips when it looks straight along its up axis. */
const POLE_MARGIN_RAD = 0.01;

/** Orbit by `right` steps around the up axis and `up` steps over the top (negative: the other way). */
export function orbitByStep(controls: CameraControls | OrbitControls, camera: THREE.Camera, right: number, up: number): void {
    const azimuth = -right * ORBIT_STEP_RAD, polar = -up * ORBIT_STEP_RAD;
    if (controls instanceof CameraControls) {
        void controls.rotate(azimuth, polar, true);
        return;
    }
    // OrbitControls has no rotate call: move the camera on its sphere around the target, in the frame
    // where the camera's up axis is +Y (what THREE.Spherical measures from).
    const toY = new THREE.Quaternion().setFromUnitVectors(camera.up, new THREE.Vector3(0, 1, 0));
    const offset = camera.position.clone().sub(controls.target).applyQuaternion(toY);
    const s = new THREE.Spherical().setFromVector3(offset);
    s.theta += azimuth;
    s.phi = Math.min(Math.PI - POLE_MARGIN_RAD, Math.max(POLE_MARGIN_RAD, s.phi + polar));
    camera.position.copy(controls.target).add(offset.setFromSpherical(s).applyQuaternion(toY.invert()));
    camera.lookAt(controls.target);
    controls.update();
}

/** Pan by `right` and `up` steps in the plane of the screen. */
export function panByStep(controls: CameraControls | OrbitControls, camera: THREE.Camera, right: number, up: number): void {
    if (controls instanceof CameraControls) {
        const step = controls.distance * PAN_STEP_FRACTION;
        void controls.truck(right * step, -up * step, true);
        return;
    }
    const step = camera.position.distanceTo(controls.target) * PAN_STEP_FRACTION;
    const x = new THREE.Vector3(), y = new THREE.Vector3();
    camera.matrixWorld.extractBasis(x, y, new THREE.Vector3());
    const move = x.multiplyScalar(right * step).add(y.multiplyScalar(up * step));
    camera.position.add(move);
    controls.target.add(move);
    controls.update();
}
