import * as THREE from "three";
import {SELECTION_COLOR_INT} from "@/ui/selectionColor";

// Colour comes from ui/selectionColor so the 3D highlight and the DOM's --ada-select
// cannot drift apart. Was the CSS keyword 'blue' (#0000FF), which is so dark it read
// as a hole in the geometry rather than a highlight.
export const selectedMaterial = new THREE.MeshStandardMaterial({color: SELECTION_COLOR_INT, side: THREE.DoubleSide});
// export const defaultMaterial = new THREE.MeshStandardMaterial({color: 'white', side: THREE.DoubleSide});
export const defaultMaterial = new THREE.MeshStandardMaterial({
      color: 0x808080, // Gray color
      metalness: 0.1,
      roughness: 0.5,
      flatShading: true, // Enable flat shading
      side: THREE.DoubleSide,
    });