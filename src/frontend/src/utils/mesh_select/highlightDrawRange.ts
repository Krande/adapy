import * as THREE from "three";
import {defaultMaterial, selectedMaterial} from "../default_materials";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {useModelStore} from "../../state/modelStore";
import {useSelectedObjectStore} from "../../state/selectedObjectStore";

export function highlightDrawRange(mesh: THREE.Mesh, drawRange: [string, number, number]): void {
    const geometry = mesh.geometry as THREE.BufferGeometry;
    if (!geometry || !drawRange) {
        console.warn("Invalid geometry or draw range");
        return;
    }

    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    if (selectedObject) {
        selectedObject.geometry.clearGroups();
    }

    useSelectedObjectStore.getState().setSelectedObject(mesh);

    const [rangeId, start, count] = drawRange;

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

    let scene = useModelStore.getState().scene;
    let hierarchy: Record<string, [string, string | number]> = scene?.userData["id_hierarchy"];
    let value = hierarchy[rangeId];
    if (value) {
        // Update the object info store
        useObjectInfoStore.getState().setName(value[0]);
    }


}
