// sceneHelpers/addCameraLight.ts
import * as THREE from "three";
import type {OwnedSceneObjects} from "@/utils/scene/ownedSceneObjects";

export function addCameraLightWithTracking(
  camera: THREE.Camera,
  owned: OwnedSceneObjects,
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
  owned.add(light);
  const target = owned.add(new THREE.Object3D());
  light.target = target;

  // Initial update
  light.position.copy(camera.position);

  const direction = new THREE.Vector3();
  // Return an updater function to call on each frame
  return () => {
    light.position.copy(camera.position);
    camera.getWorldDirection(direction);
    target.position.copy(camera.position).add(direction);
    light.target.updateMatrixWorld();
  };
}
