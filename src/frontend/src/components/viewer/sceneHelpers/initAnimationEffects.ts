// sceneHelpers/initAnimationEffects.ts
import * as THREE from "three";
import { useAnimationStore } from "../../../state/animationStore";

export function initAnimationEffects(
  animations: THREE.AnimationClip[],
  scene: THREE.Object3D
): THREE.AnimationMixer {
  const mixer = new THREE.AnimationMixer(scene);

  const store = useAnimationStore.getState();
  store.setMixer(mixer);
  store.setAnimations(animations);

  return mixer;
}
