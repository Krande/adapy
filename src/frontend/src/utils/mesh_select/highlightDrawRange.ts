import * as THREE from "three";
import { selectedMaterial } from "../default_materials";

export function highlightDrawRange(mesh: THREE.Mesh, drawRange: [number, number]): void {
    const geometry = mesh.geometry as THREE.BufferGeometry;
    if (!geometry || !drawRange) {
        console.warn("Invalid geometry or draw range");
        return;
    }

    const [start, count] = drawRange;
    console.log("highlightDrawRange", start, count, mesh.name);
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
