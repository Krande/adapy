import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import * as THREE from "three";
import CameraControls from "camera-controls";
import {ensureViewportInput} from "@/viewport/input";

CameraControls.install({THREE: THREE});

export function setupControls(
    camera: THREE.PerspectiveCamera,
    container: HTMLDivElement,
    zIsUp: boolean,
    defaultOrbitController: boolean
): CameraControls | OrbitControls {

    // Controls bind to the viewport's input surface, not to a renderer canvas: the same input then
    // drives the camera whether three.js or the adacpp WebGPU renderer draws.
    const canvas = ensureViewportInput(container);

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
