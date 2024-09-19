// modelStore.ts
import { create } from 'zustand';
import { SceneAction } from '../utils/handleWebSocketMessage';
import * as THREE from 'three';

interface ModelState {
  modelUrl: string | null;
  scene_action: SceneAction | null;
  scene_action_arg: string | null;
  translation: THREE.Vector3 | null;
  boundingBox: THREE.Box3 | null;
  setModelUrl: (
    url: string | null,
    scene_action: SceneAction | null,
    scene_action_arg: string | null
  ) => void;
  setTranslation: (translation: THREE.Vector3) => void;
  setBoundingBox: (boundingBox: THREE.Box3) => void;
}

export const useModelStore = create<ModelState>((set) => ({
  modelUrl: null,
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
}));
