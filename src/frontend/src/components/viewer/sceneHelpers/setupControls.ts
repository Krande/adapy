import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import * as THREE from "three";
import CameraControls from "camera-controls";

CameraControls.install({THREE: THREE});

export function setupControls(
    camera: THREE.PerspectiveCamera,
    container: HTMLDivElement,
    zIsUp: boolean,
    defaultOrbitController: boolean
): CameraControls | OrbitControls {

    const canvas = container.querySelector("canvas");
    if (!canvas) throw new Error("Renderer canvas not found");

    let controls: CameraControls | OrbitControls;
    if (defaultOrbitController) {
        controls = new OrbitControls(camera, canvas);
        console.log("Using OrbitControls");
    } else {
        controls = new CameraControls(camera, canvas);
        console.log("Using CameraControls");
    }

    return controls
}
