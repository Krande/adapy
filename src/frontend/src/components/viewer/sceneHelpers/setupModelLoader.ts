import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader";
import { prepareLoadedModel } from "./prepareLoadedModel";
import { useModelStore } from "../../../state/modelStore";
import { useOptionsStore } from "../../../state/optionsStore";
import { useTreeViewStore } from "../../../state/treeViewStore";
import { useAnimationStore } from "../../../state/animationStore";
import { initAnimationEffects } from "./initAnimationEffects";

export function setupModelLoader(
  scene: THREE.Scene,
  modelUrl: string | null,
): void {
  if (!modelUrl) return;

  const loader = new GLTFLoader();
  loader.load(
    modelUrl,
    (gltf) => {
      const loadedScene = gltf.scene;
      const modelGroup = new THREE.Group();
      modelGroup.add(loadedScene);
      scene.add(modelGroup);

      prepareLoadedModel({
        scene: loadedScene,
        modelStore: useModelStore.getState(),
        optionsStore: useOptionsStore.getState(),
        treeViewStore: useTreeViewStore.getState(),
        animationStore: useAnimationStore.getState(),
      });

      const animations = gltf.animations;
      if (animations.length > 0) {
        const mixer = initAnimationEffects(animations, loadedScene);
        const a0 = animations[0];
        const action = mixer.clipAction(a0);
        action.play();
        useAnimationStore.getState().setAction(action);
        useAnimationStore.getState().setSelectedAnimation(a0.name);
        useAnimationStore.getState().setCurrentKey(0);
      }
    },
    undefined,
    (error) => {
      console.error("Error loading model:", error);
    },
  );
}
