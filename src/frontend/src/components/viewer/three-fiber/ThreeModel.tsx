import React, { useEffect } from "react";
import { useGLTF } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { GLTFResult, ModelProps } from "../../../state/modelInterfaces";
import { useModelStore } from "../../../state/modelStore";
import { useTreeViewStore } from "../../../state/treeViewStore";
import { useOptionsStore } from "../../../state/optionsStore";
import { useAnimationStore } from "../../../state/animationStore";
import { useAnimationEffects } from "../../../hooks/useAnimationEffects";
import { handleClickMeshFiber } from "./handleClickMeshFiber";
import { prepareLoadedModel } from "../sceneHelpers/prepareLoadedModel";

const ThreeModel: React.FC<ModelProps> = ({ url }) => {
  const { scene, animations } = useGLTF(url, false) as unknown as GLTFResult;
  const modelStore = useModelStore();
  const treeViewStore = useTreeViewStore();
  const optionsStore = useOptionsStore();
  const animationStore = useAnimationStore();

  useAnimationEffects(animations, scene);

  useEffect(() => {
    if (optionsStore.useVanillaThree) {
      return;
    }
    prepareLoadedModel({
      scene,
      modelStore,
      treeViewStore,
      optionsStore,
      animationStore,
    });

    return () => {
      treeViewStore.clearTreeData();
      useModelStore.getState().setScene(null);
      useModelStore.getState().setRaycaster(null);
    };
  }, [scene]);

  useFrame((_, delta) => {
    if (animationStore.action) {
      animationStore.action.getMixer().update(delta);
      animationStore.setCurrentKey(animationStore.action.time);
    }
  });

  return (
    <primitive object={scene} onPointerDown={handleClickMeshFiber} dispose={null} />
  );
};

export default ThreeModel;
