import * as THREE from "three";
import {SceneAction} from "../utils/handleWebSocketMessage";


export interface GLTFResult {
    scene: THREE.Scene;
    animations: THREE.AnimationClip[];
}

export interface ModelProps {
    url: string;
    scene_action: SceneAction | null;
    scene_action_arg: string | null;
}