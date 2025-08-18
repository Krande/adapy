import * as THREE from "three";
import {sceneRef, selectedPointRef} from "../../state/refs";

// Configuration for selection display method
export function clearSelectedPoint() {
    const scene = sceneRef.current;
    const hl = selectedPointRef.current;
    if (!scene || !hl) return;

    scene.remove(hl);
    if (hl.geometry) hl.geometry.dispose();
    if (Array.isArray(hl.material)) {
        hl.material.forEach(m => m.dispose());
    } else {
        (hl.material as THREE.Material).dispose();
    }
    selectedPointRef.current = null;
}


