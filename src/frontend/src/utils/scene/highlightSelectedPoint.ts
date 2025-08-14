import * as THREE from "three";
import {sceneRef, selectedPointRef, rendererRef, cameraRef} from "../../state/refs";
import {createSphericalPointMaterial} from "./pointsImpostor";
import {selectedMaterial} from "../default_materials";
import {useOptionsStore} from "../../state/optionsStore";

export function showSelectedPoint(worldPosition: THREE.Vector3, size: number) {
    const scene = sceneRef.current;
    if (!scene) return;

    // Determine highlight color from the shared selected material
    const highlightColor = selectedMaterial.color; // THREE.Color

    // Read sizing mode and viewport info
    const opts = useOptionsStore.getState();
    const absolute = !!opts.pointSizeAbsolute;
    const renderer = rendererRef.current;
    const cam = cameraRef.current as THREE.PerspectiveCamera | null;
    const viewportHeight = renderer ? renderer.getSize(new THREE.Vector2()).y : window.innerHeight;
    const fov = cam && (cam as any).isPerspectiveCamera ? cam.fov : 50.0;

    // Create or update the singleton highlight points object
    let hl = selectedPointRef.current;
    const displaySize = Math.max(size * 1.5, size + 0.01);
    if (!hl) {
        const geom = new THREE.BufferGeometry();
        geom.setAttribute('position', new THREE.Float32BufferAttribute([worldPosition.x, worldPosition.y, worldPosition.z], 3));
        const mat = createSphericalPointMaterial({ pointSize: displaySize, color: highlightColor, opacity: 1.0, useVertexColors: false, depthTest: true, depthWrite: true });
        // Initialize sizing uniforms for immediate correctness
        if ((mat as any).uniforms) {
            const u = (mat as any).uniforms;
            if (u.pointSize) u.pointSize.value = displaySize;
            if (u.uWorldSize) u.uWorldSize.value = absolute;
            if (u.uWorldPointSize) u.uWorldPointSize.value = displaySize;
            if (u.uFov) u.uFov.value = fov;
            if (u.uViewportHeight) u.uViewportHeight.value = viewportHeight;
            if (u.uColor) u.uColor.value = highlightColor;
            (mat as any).needsUpdate = true;
        }
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
            if (mat.uniforms.pointSize) mat.uniforms.pointSize.value = displaySize;
            if (mat.uniforms.uWorldSize) mat.uniforms.uWorldSize.value = absolute;
            if (mat.uniforms.uWorldPointSize) mat.uniforms.uWorldPointSize.value = displaySize;
            if (mat.uniforms.uFov) mat.uniforms.uFov.value = fov;
            if (mat.uniforms.uViewportHeight) mat.uniforms.uViewportHeight.value = viewportHeight;
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
