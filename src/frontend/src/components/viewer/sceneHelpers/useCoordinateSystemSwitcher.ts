import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls";

export function applyCoordinateSystem(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  scene: THREE.Scene,
  zIsUp: boolean
) {
  const up = zIsUp ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
  camera.up.copy(up);
  controls.object.up.copy(up);
  controls.screenSpacePanning = !zIsUp;

  const distance = camera.position.length();
  const direction = new THREE.Vector3(1, 1, 1).normalize();
  camera.position.copy(direction.multiplyScalar(distance));
  controls.target.set(0, 0, 0);
  camera.lookAt(controls.target);
  controls.update();
  camera.updateProjectionMatrix();

  const grid = scene.children.find(
    (child) => child instanceof THREE.GridHelper
  ) as THREE.GridHelper | undefined;

  if (grid) {
    grid.rotation.set(zIsUp ? Math.PI / 2 : 0, 0, 0);
  }
}
