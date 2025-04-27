import {OrientationGizmo} from "./OrientationGizmo";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import * as THREE from "three";
import {addOrientationGizmo} from "./addOrientationGizmo";
import CameraControls from "camera-controls";

export function setupGizmo(
    camera: THREE.PerspectiveCamera,
    container: HTMLDivElement,
    controls: OrbitControls | CameraControls,
): OrientationGizmo {
    const gizmo = addOrientationGizmo(camera, container);

    // @ts-ignore
    gizmo.onAxisSelected = ({axis, direction}) => {
        const distance = camera.position.length(); // maintain distance
        camera.position.copy(direction.clone().multiplyScalar(distance));
        if (controls instanceof OrbitControls) {
            controls.target.set(0, 0, 0);
            controls.update();
        }
    };

    return gizmo;
}
