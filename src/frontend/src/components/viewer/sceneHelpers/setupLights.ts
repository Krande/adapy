import * as THREE from "three";
import type {OwnedSceneObjects} from "@/utils/scene/ownedSceneObjects";
import { addCameraLightWithTracking } from "./addCameraLight";

/** The viewer's lighting, added through `owned` so a remount replaces it rather than adding to it. */
export function setupLights(owned: OwnedSceneObjects, camera: THREE.Camera) {
  owned.add(new THREE.AmbientLight(0xffffff, Math.PI / 2));

  const updateCameraLight = addCameraLightWithTracking(camera, owned);

  return {
    updateCameraLight,
  };
}
