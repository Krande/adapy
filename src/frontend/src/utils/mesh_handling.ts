import {useSelectedObjectStore} from "../state/selectedObjectStore";
import * as THREE from "three";
import {ThreeEvent} from "@react-three/fiber";

export function handleMeshSelected(event: ThreeEvent<PointerEvent>) {
    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalColor = useSelectedObjectStore.getState().originalColor;
    const mesh = event.object as THREE.Mesh;
    if (selectedObject !== mesh) {
        if (selectedObject) {
            (selectedObject.material as THREE.MeshBasicMaterial).color.set(originalColor || 'white');
        }

        const material = mesh.material as THREE.MeshBasicMaterial;
        useSelectedObjectStore.getState().setOriginalColor(material.color);
        useSelectedObjectStore.getState().setSelectedObject(mesh);
        const meshInfo = {
            name: mesh.name,
            materialName: material.name,
            intersectionPoint: event.point,
            faceIndex: event.faceIndex || 0,
            meshClicked: true,
        };

        console.log('mesh clicked');
        console.log(meshInfo);
        console.log(event);
    }
}

export function handleMeshEmptySpace(event: MouseEvent) {
    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalColor = useSelectedObjectStore.getState().originalColor;

    console.log('click on empty space');
    if (selectedObject) {
        console.log(`deselecting object. Reverting to original color ${originalColor}`);
        (selectedObject.material as THREE.MeshBasicMaterial).color.set(originalColor || 'white');
        useSelectedObjectStore.getState().setSelectedObject(null);
    }
}
