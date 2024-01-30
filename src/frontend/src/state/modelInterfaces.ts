import * as THREE from "three";


export interface GLTFResult {
    scene: THREE.Scene;
    animations: THREE.AnimationClip[];
}

export interface ModelProps {
    url: string;
    onMeshSelected: (meshInfo: any) => void;
}