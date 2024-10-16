import * as THREE from "three";
import {SceneOperations} from "../flatbuffers/wsock/scene-operations";



export interface GLTFResult {
    scene: THREE.Scene;
    animations: THREE.AnimationClip[];
}

export interface ModelProps {
    url: string;
    scene_action: SceneOperations | null;
    scene_action_arg: string | null;
}