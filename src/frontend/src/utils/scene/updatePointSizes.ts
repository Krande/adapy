import * as THREE from "three";
import {sceneRef} from "../../state/refs";

export function updateAllPointsSize(size: number) {
    const scene = sceneRef.current;
    if (!scene) return;

    scene.traverse(obj => {
        if (obj instanceof THREE.Points) {
            const mat = obj.material as THREE.Material | THREE.Material[];
            const applySize = (m: THREE.Material) => {
                // PointsMaterial
                if ((m as any).isPointsMaterial) {
                    const pm = m as THREE.PointsMaterial;
                    pm.size = size;
                    pm.sizeAttenuation = true;
                    pm.needsUpdate = true;
                }
                // ShaderMaterial with pointSize uniform
                else if ((m as any).isShaderMaterial) {
                    const sm = m as THREE.ShaderMaterial & { uniforms?: any };
                    if (sm.uniforms && sm.uniforms.pointSize) {
                        sm.uniforms.pointSize.value = size;
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
