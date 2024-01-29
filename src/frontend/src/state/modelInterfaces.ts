import * as THREE from "three";

export interface MeshInfo {
    name: string;
    materialName: string;
    intersectionPoint: THREE.Vector3;
    faceIndex: number;
    meshClicked: boolean;
}

export interface GLTFResult {
    scene: THREE.Scene;
    animations: THREE.AnimationClip[];
}

export interface ModelProps {
    url: string;
    onMeshSelected: (meshInfo: any) => void;
}