import * as THREE from "three";
import type {OwnedSceneObjects} from "@/utils/scene/ownedSceneObjects";
import {lightIntensities, useAppearanceStore} from "@/state/appearanceStore";
import {requestRender} from "@/state/perfStore";
import { addCameraLightWithTracking } from "./addCameraLight";

/** The viewer's lighting, added through `owned` so a remount replaces it rather than adding to it:
 *  an ambient light and a headlight, as bright as the appearance settings say
 *  (state/appearanceStore.ts). `release` stops them following the settings. */
export function setupLights(owned: OwnedSceneObjects, camera: THREE.Camera) {
  const {ambient, headlight} = lightIntensities();
  const ambientLight = owned.add(new THREE.AmbientLight(0xffffff, ambient));

  const {update: updateCameraLight, light} = addCameraLightWithTracking(camera, owned, headlight);

  // A change of the settings is two numbers on the lights: nothing is rebuilt.
  const release = useAppearanceStore.subscribe((s, prev) => {
    if (s.brightness === prev.brightness && s.ambientShare === prev.ambientShare) return;
    const now = lightIntensities(s);
    ambientLight.intensity = now.ambient;
    light.intensity = now.headlight;
    requestRender();
  });

  return {
    updateCameraLight,
    release,
  };
}
