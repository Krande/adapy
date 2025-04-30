import * as THREE from "three";
import { addCameraLightWithTracking } from "./addCameraLight";

export function setupLights(scene: THREE.Scene, camera: THREE.Camera) {
  const ambientLight = new THREE.AmbientLight(0xffffff, Math.PI / 2);
  scene.add(ambientLight);

  const updateCameraLight = addCameraLightWithTracking(camera, scene);

  return {
    updateCameraLight,
  };
}
