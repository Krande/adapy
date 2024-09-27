// replaceBlackMaterials.ts
import * as THREE from 'three';

export function replaceBlackMaterials(object: THREE.Object3D) {
    const defaultMaterial = new THREE.MeshStandardMaterial({
      color: 0x808080, // Gray color
      metalness: 0.1,
      roughness: 0.5,
      flatShading: true, // Enable flat shading
    });
    defaultMaterial.side = THREE.DoubleSide;

  object.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      const material = child.material;

      // Handle both array of materials and single material
      const materials = Array.isArray(material) ? material : [material];

      materials.forEach((mat, index) => {
        if (mat instanceof THREE.Material) {
          let replace = false;

          // Check if material is a MeshBasicMaterial, MeshStandardMaterial, or similar
          if (
            mat instanceof THREE.MeshBasicMaterial ||
            mat instanceof THREE.MeshStandardMaterial ||
            mat instanceof THREE.MeshLambertMaterial ||
            mat instanceof THREE.MeshPhongMaterial ||
            mat instanceof THREE.MeshPhysicalMaterial ||
            mat instanceof THREE.MeshToonMaterial
          ) {
            const color = (mat as THREE.MeshStandardMaterial).color;
            const emissive = (mat as THREE.MeshStandardMaterial).emissive;

            // Check if the material color is black (0x000000)
            if (color.equals(new THREE.Color(0x000000)) && emissive.equals(new THREE.Color(0x000000))) {
              replace = true;
            }
          }

          // Replace the material if it's black
          if (replace) {
            materials[index] = defaultMaterial;
          }
          // add flatshading to all materials
          materials[index].flatShading = true;
        }
      });

      // Assign the updated materials back to the mesh
      child.material = Array.isArray(material) ? materials : materials[0];
    }
  });
}
