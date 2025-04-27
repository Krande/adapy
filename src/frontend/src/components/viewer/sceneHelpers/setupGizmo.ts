import {OrientationGizmo} from "./OrientationGizmo";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import * as THREE from "three";
import {addOrientationGizmo} from "./addOrientationGizmo";

export function setupGizmo(
    camera: THREE.PerspectiveCamera,
    container: HTMLDivElement,
    controls: OrbitControls,
): OrientationGizmo {
    const gizmo = addOrientationGizmo(camera, container);

    // @ts-ignore
    gizmo.onAxisSelected = ({axis, direction}) => {
        const distance = camera.position.length(); // maintain distance
        camera.position.copy(direction.clone().multiplyScalar(distance));
        controls.target.set(0, 0, 0);
        controls.update();
    };

    return gizmo;
}
