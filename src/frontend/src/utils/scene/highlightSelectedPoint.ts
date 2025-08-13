import * as THREE from "three";
import {sceneRef, selectedPointRef} from "../../state/refs";
import {createSphericalPointMaterial} from "./pointsImpostor";
import {selectedMaterial} from "../default_materials";

export function showSelectedPoint(worldPosition: THREE.Vector3, size: number) {
    const scene = sceneRef.current;
    if (!scene) return;

    // Determine highlight color from the shared selected material
    const highlightColor = selectedMaterial.color; // THREE.Color

    // Create or update the singleton highlight points object
    let hl = selectedPointRef.current;
    const displaySize = Math.max(size * 1.5, size + 0.01);
    if (!hl) {
        const geom = new THREE.BufferGeometry();
        geom.setAttribute('position', new THREE.Float32BufferAttribute([worldPosition.x, worldPosition.y, worldPosition.z], 3));
        const mat = createSphericalPointMaterial({ pointSize: displaySize, color: highlightColor, opacity: 1.0, useVertexColors: false, depthTest: false, depthWrite: false });
        hl = new THREE.Points(geom, mat);
        hl.renderOrder = 9999; // draw on top (helps visibility)
        selectedPointRef.current = hl;
        scene.add(hl);
    } else {
        const geom = hl.geometry as THREE.BufferGeometry;
        const posAttr = geom.getAttribute('position') as THREE.BufferAttribute;
        if (posAttr && posAttr.count === 1) {
            posAttr.setXYZ(0, worldPosition.x, worldPosition.y, worldPosition.z);
            posAttr.needsUpdate = true;
        } else {
            geom.setAttribute('position', new THREE.Float32BufferAttribute([worldPosition.x, worldPosition.y, worldPosition.z], 3));
        }
        const mat = hl.material as THREE.ShaderMaterial & { uniforms?: any };
        if (mat.uniforms) {
            if (mat.uniforms.pointSize) {
                mat.uniforms.pointSize.value = displaySize;
            }
            if (mat.uniforms.uColor) {
                // Keep color in sync if the global selected color changes
                mat.uniforms.uColor.value = highlightColor;
            }
            mat.needsUpdate = true;
        }
    }
}

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
