// modelStore.ts
import { create } from 'zustand';
import * as THREE from 'three';
import {SceneOperations} from "../flatbuffers/wsock/scene-operations";

interface ModelState {
  modelUrl: string | null;
  scene: THREE.Scene | null;
  scene_action: SceneOperations | null;
  scene_action_arg: string | null;
  translation: THREE.Vector3 | null;
  boundingBox: THREE.Box3 | null;
  setModelUrl: (
    url: string | null,
    scene_action: SceneOperations | null,
    scene_action_arg: string | null
  ) => void;
  setTranslation: (translation: THREE.Vector3) => void;
  setBoundingBox: (boundingBox: THREE.Box3) => void;
  setScene: (scene: THREE.Scene) => void;
}

export const useModelStore = create<ModelState>((set) => ({
  modelUrl: null,
  scene: null,
  scene_action: null,
  scene_action_arg: null,
  translation: null,
  boundingBox: null,
  setModelUrl: (url, scene_action, scene_action_arg) =>
    set({
      modelUrl: url,
      scene_action: scene_action,
      scene_action_arg: scene_action_arg,
    }),
  setTranslation: (translation) => set({ translation }),
  setBoundingBox: (boundingBox) => set({ boundingBox }),
  setScene: (scene: THREE.Scene) => set({ scene }),
}));
