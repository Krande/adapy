import {useSelectedObjectStore} from "../state/selectedObjectStore";
import * as THREE from "three";
import {ThreeEvent} from "@react-three/fiber";
import {useObjectInfoStore} from "../state/objectInfoStore";


const selectedMaterial = new THREE.MeshStandardMaterial({color: 'blue', side: THREE.DoubleSide});
const defaultMaterial = new THREE.MeshStandardMaterial({color: 'white', side: THREE.DoubleSide});


function deselectObject() {
    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalMaterial = useSelectedObjectStore.getState().originalMaterial;
    if (selectedObject) {
        selectedObject.material = originalMaterial? originalMaterial : defaultMaterial;
        useSelectedObjectStore.getState().setOriginalMaterial(null);
        useSelectedObjectStore.getState().setSelectedObject(null);
    }
}


export function handleMeshSelected(event: ThreeEvent<PointerEvent>) {
    event.stopPropagation();

    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalMaterial = useSelectedObjectStore.getState().originalMaterial;
    const mesh = event.object as THREE.Mesh;

    if (selectedObject !== mesh) {
        if (selectedObject) {
            selectedObject.material = originalMaterial? originalMaterial : defaultMaterial;
        }
        const material = mesh.material as THREE.MeshBasicMaterial;
        useSelectedObjectStore.getState().setOriginalMaterial(mesh.material? material : null);
        useSelectedObjectStore.getState().setSelectedObject(mesh);

        // Update the object info store
        useObjectInfoStore.getState().setName(mesh.name);
        useObjectInfoStore.getState().setFaceIndex(event.faceIndex || 0);

        // Create a new material for the selected mesh
        mesh.material = selectedMaterial;
    } else {
        deselectObject();
    }

}

export function handleMeshEmptySpace(event: MouseEvent) {
    event.stopPropagation();
    deselectObject();
}
