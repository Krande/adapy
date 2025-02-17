import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";
import * as THREE from "three";

export function convert_to_custom_batch_mesh(original: THREE.Mesh, drawRanges: Map<string, [number, number]>) {
    const customMesh = new CustomBatchedMesh(
        original.geometry,
        original.material,
        drawRanges
    );

    // Copy over properties from original mesh to customMesh
    customMesh.position.copy(original.position);
    customMesh.rotation.copy(original.rotation);
    customMesh.scale.copy(original.scale);
    customMesh.name = original.name;
    customMesh.userData = original.userData;
    customMesh.castShadow = original.castShadow;
    customMesh.receiveShadow = original.receiveShadow;
    customMesh.visible = original.visible;
    customMesh.frustumCulled = original.frustumCulled;
    customMesh.renderOrder = original.renderOrder;
    customMesh.layers.mask = original.layers.mask;

    // Set materials to double-sided and enable flat shading
    if (Array.isArray(customMesh.material)) {
        customMesh.material.forEach((mat) => {
            if (mat instanceof THREE.MeshStandardMaterial) {
                mat.side = THREE.DoubleSide;
                mat.flatShading = true;
                mat.needsUpdate = true;
            } else {
                console.warn('Material is not an instance of MeshStandardMaterial');
            }
        });
    } else {
        if (customMesh.material instanceof THREE.MeshStandardMaterial) {
            customMesh.material.side = THREE.DoubleSide;
            customMesh.material.flatShading = true;
            customMesh.material.needsUpdate = true;
        } else {
            console.warn('Material is not an instance of MeshStandardMaterial');
        }
    }

    return customMesh;
}