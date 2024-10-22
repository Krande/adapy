import * as THREE from "three";
import {defaultMaterial, selectedMaterial} from "../default_materials";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {useModelStore} from "../../state/modelStore";
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import {exists} from "node:fs";

export function highlightDrawRange(mesh: THREE.Mesh, drawRange: [string, number, number]): void {
    const geometry = mesh.geometry as THREE.BufferGeometry;
    if (!geometry || !drawRange) {
        console.warn("Invalid geometry or draw range");
        return;
    }

    const alreadySelectedObject = useSelectedObjectStore.getState().selectedObject;
    if (alreadySelectedObject) {
        if (alreadySelectedObject.geometry) {
            alreadySelectedObject.geometry.clearGroups();
        }

        let existing_mat = useSelectedObjectStore.getState().originalMaterial
        if (alreadySelectedObject.material && existing_mat) {
            alreadySelectedObject.material = existing_mat;
        }

    }

    useSelectedObjectStore.getState().setSelectedObject(mesh);
    useSelectedObjectStore.getState().setOriginalMaterial(mesh.material as THREE.MeshBasicMaterial);

    const [rangeId, start, count] = drawRange;

    // Clear existing groups
    geometry.clearGroups();

    // Add the original group (everything before the highlight)
    if (start > 0) {
        geometry.addGroup(0, start, 0); // Material index 0 for the original material
    }

    // Add the highlighted group
    geometry.addGroup(start, count, 1); // Material index 1 for the selected material

    // Add the rest of the mesh
    if (start + count < geometry.index!.count) {
        geometry.addGroup(start + count, geometry.index!.count - (start + count), 0);
    }

    // Create or update the materials array
    const originalMaterial = (mesh.material as THREE.Material[])[0] || (mesh.material as THREE.Material);

    // Set the materials array with the original and selected materials
    mesh.material = [originalMaterial, selectedMaterial];
    // Set needsUpdate for each material
    mesh.material.forEach(material => {
        material.needsUpdate = true;
    });



}
