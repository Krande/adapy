// handleClick.ts
import * as THREE from 'three';
import { ThreeEvent } from "@react-three/fiber";
import React from "react";

interface MeshInfo {
    name: string;
    materialName: string;
    intersectionPoint: THREE.Vector3;
    faceIndex: number;
    meshClicked: boolean;
}

export const handleClick = (
    event: ThreeEvent<PointerEvent>,
    selectedObject: THREE.Mesh | null,
    setSelectedObject: React.Dispatch<React.SetStateAction<THREE.Mesh | null>>,
    onMeshSelected: (meshInfo: MeshInfo) => void
) => {
    event.stopPropagation();
    const mesh = event.object as THREE.Mesh;
    if (selectedObject !== mesh) {
        if (selectedObject) {
            (selectedObject.material as THREE.MeshBasicMaterial).color.set(selectedObject.userData.originalColor || 'white');
        }

        const material = mesh.material as THREE.MeshBasicMaterial;
        material.color.set(mesh.userData.originalColor || 'white');
        mesh.userData.originalColor = material.color.getHex();
        setSelectedObject(mesh);

        const meshInfo = {
            name: mesh.name,
            materialName: material.name,
            intersectionPoint: event.point,
            faceIndex: event.faceIndex || 0,
            meshClicked: true,
        };
        onMeshSelected(meshInfo);
    }
};

export const handleClickEmptySpace = (
    event: MouseEvent,
    selectedObject: THREE.Mesh | null,
    setSelectedObject: React.Dispatch<React.SetStateAction<THREE.Mesh | null>>
) => {
    event.stopPropagation();
    console.log('click on empty space');
    if (selectedObject) {
        (selectedObject.material as THREE.MeshBasicMaterial).color.set(selectedObject.userData.originalColor || 'white');
        setSelectedObject(null);
    }
}