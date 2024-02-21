import {create} from 'zustand';
import {SceneAction} from "../utils/handleWebSocketMessage";

interface ModelState {
    modelUrl: string | null;
    scene_action: SceneAction | null;
    scene_action_arg: string | null;
    setModelUrl: (url: string | null, scene_action: SceneAction | null, scene_action_arg: string | null) => void;
}

export const useModelStore = create<ModelState>((set) => ({
    modelUrl: null,
    scene_action: null,
    scene_action_arg: null,
    setModelUrl: (url, scene_action, scene_action_arg) => set({
        modelUrl: url,
        scene_action: scene_action,
        scene_action_arg: scene_action_arg
    }),
}));
