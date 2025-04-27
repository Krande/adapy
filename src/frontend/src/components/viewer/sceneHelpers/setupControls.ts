import { OrbitControls } from "three/examples/jsm/controls/OrbitControls";
import * as THREE from "three";
import {ZUpOrbitControls} from "./ZUpOrbitControls";

export function setupControls(
  camera: THREE.PerspectiveCamera,
  container: HTMLDivElement,
 zIsUp: boolean,
): OrbitControls {
  const canvas = container.querySelector("canvas");
  if (!canvas) throw new Error("Renderer canvas not found");

  const controls = new ZUpOrbitControls(camera, canvas);
  controls.enableDamping = false;
  controls.enablePan = true;
  controls.enableZoom = true;
  controls.screenSpacePanning = !zIsUp;
  controls.update();

    // === Monkey patch if Z is up ===
  // if (zIsUp) {
  //   patchPanUpForZUp(controls);
  // }

  return controls;
}

// ===== Internal helper
interface PatchedOrbitControls extends OrbitControls {
  panUp: (distance: number, objectMatrix: THREE.Matrix4) => void;
  panOffset: THREE.Vector3;
}
function patchPanUpForZUp(controls: OrbitControls) {
  const patched = controls as PatchedOrbitControls;

  patched.panUp = function (distance: number, objectMatrix: THREE.Matrix4) {
    const v = new THREE.Vector3();

    if (this.screenSpacePanning) {
      v.setFromMatrixColumn(objectMatrix, 1);
    } else {
      v.setFromMatrixColumn(objectMatrix, 0);
      v.crossVectors(v, this.object.up);
    }

    v.multiplyScalar(distance);
    this.panOffset.add(v);
  };
}
