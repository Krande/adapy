// sceneHelpers/addCameraLight.ts
import * as THREE from "three";

export function addCameraLightWithTracking(
  camera: THREE.Camera,
  scene: THREE.Scene
): () => void {
  const light = new THREE.DirectionalLight(0xffffff, 1.4);
  light.castShadow = false;

  // Optional: enable shadows if needed
  light.shadow.mapSize.width = 2048;
  light.shadow.mapSize.height = 2048;
  light.shadow.camera.near = 0.5;
  light.shadow.camera.far = 500;
  light.shadow.bias = -0.0001;

  // Add the light and its target to the scene
  scene.add(light);
  const target = new THREE.Object3D();
  scene.add(target);
  light.target = target;

  // Initial update
  light.position.copy(camera.position);

  // Return an updater function to call on each frame
  return () => {
    light.position.copy(camera.position);

    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction);

    target.position.copy(camera.position.clone().add(direction));
    light.target.updateMatrixWorld();
  };
}
