import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import * as THREE from "three";
import CameraControls from "camera-controls";
CameraControls.install( { THREE: THREE } );

export function setupControls(
    camera: THREE.PerspectiveCamera,
    container: HTMLDivElement,
    zIsUp: boolean,
): CameraControls | OrbitControls {
    const canvas = container.querySelector("canvas");
    if (!canvas) throw new Error("Renderer canvas not found");

    return new CameraControls(camera, canvas);
}
