import * as THREE from "three";

export const selectedMaterial = new THREE.MeshStandardMaterial({color: 'blue', side: THREE.DoubleSide});
// export const defaultMaterial = new THREE.MeshStandardMaterial({color: 'white', side: THREE.DoubleSide});
export const defaultMaterial = new THREE.MeshStandardMaterial({
      color: 0x808080, // Gray color
      metalness: 0.1,
      roughness: 0.5,
      flatShading: true, // Enable flat shading
      side: THREE.DoubleSide,
    });