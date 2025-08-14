import * as THREE from "three";
import {sceneRef, rendererRef, cameraRef} from "../../state/refs";

export function updateAllPointsSize(size: number, absolute?: boolean) {
    const scene = sceneRef.current;
    if (!scene) return;

    const renderer = rendererRef.current;
    const cam = cameraRef.current;
    const viewportHeight = renderer ? renderer.getSize(new THREE.Vector2()).y : window.innerHeight;
    const fov = (cam && (cam as any).isPerspectiveCamera) ? (cam as THREE.PerspectiveCamera).fov : 50.0;

    scene.traverse(obj => {
        if (obj instanceof THREE.Points) {
            const mat = obj.material as THREE.Material | THREE.Material[];
            const applySize = (m: THREE.Material) => {
                // PointsMaterial fallback
                if ((m as any).isPointsMaterial) {
                    const pm = m as THREE.PointsMaterial;
                    pm.size = size;
                    // Approximate: for absolute sizing, allow attenuation with distance
                    // (screen-space when false)
                    pm.sizeAttenuation = !!absolute;
                    pm.needsUpdate = true;
                }
                // ShaderMaterial with extended uniforms
                else if ((m as any).isShaderMaterial) {
                    const sm = m as THREE.ShaderMaterial & { uniforms?: any };
                    if (sm.uniforms) {
                        if (sm.uniforms.pointSize) sm.uniforms.pointSize.value = size;
                        if (typeof absolute === 'boolean' && sm.uniforms.uWorldSize) sm.uniforms.uWorldSize.value = absolute;
                        if (sm.uniforms.uWorldPointSize) sm.uniforms.uWorldPointSize.value = size;
                        if (sm.uniforms.uFov) sm.uniforms.uFov.value = fov;
                        if (sm.uniforms.uViewportHeight) sm.uniforms.uViewportHeight.value = viewportHeight;
                        sm.needsUpdate = true;
                    }
                }
            };
            if (Array.isArray(mat)) {
                mat.forEach(applySize);
            } else if (mat) {
                applySize(mat);
            }
        }
    });
}
