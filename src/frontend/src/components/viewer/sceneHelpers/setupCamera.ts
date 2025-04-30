import * as THREE from "three";

export function setupCamera(
  container: HTMLDivElement,
  zIsUp: boolean,
): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(
    60,
    container.clientWidth / container.clientHeight,
    0.1,
    10000,
  );
  camera.position.set(-5, 5, 5);
  camera.layers.enable(0);
  camera.layers.enable(1);

  const up = zIsUp ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
  camera.up.copy(up);
  camera.updateProjectionMatrix();

  return camera;
}
