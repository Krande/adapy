// The appearance settings (state/appearanceStore.ts) on the three.js scene: its background, and the
// colour of geometry that has none. The lights follow the settings where they are made
// (components/viewer/sceneHelpers/setupLights.ts), a painted result where its material is
// (utils/scene/fea/threeResultLook.ts).

import * as THREE from "three";

import {defaultColorLinear, useAppearanceStore} from "@/state/appearanceStore";
import {requestRender} from "@/state/perfStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {hasDefaultColor} from "./defaultBlackColor";

/** Give every material of `root` that has no colour of its own the default colour as it is now.
 *  One walk over the meshes; a colour is three numbers on a material, and no geometry is touched
 *  unless the mesh has recoloured objects (its colours are then per vertex). */
export function applyDefaultColor(root: THREE.Object3D): void {
    const [r, g, b] = defaultColorLinear();
    root.traverse((o) => {
        if (o instanceof CustomBatchedMesh) {
            if (hasDefaultColor(o.originalMaterial)) o.setOwnColor(r, g, b);
            return;
        }
        const m = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
        if (!m) return;
        for (const mat of Array.isArray(m) ? m : [m]) {
            if (hasDefaultColor(mat)) (mat as THREE.Material & {color: THREE.Color}).color.setRGB(r, g, b, THREE.LinearSRGBColorSpace);
        }
    });
}

/** Keep `scene` in step with the appearance settings. Returns the function that stops it. */
export function bindThreeAppearance(scene: THREE.Scene): () => void {
    const background = (hex: string) => {
        if (scene.background instanceof THREE.Color) scene.background.set(hex);
        else scene.background = new THREE.Color(hex);
    };
    background(useAppearanceStore.getState().background);
    return useAppearanceStore.subscribe((s, prev) => {
        if (s.background !== prev.background) background(s.background);
        if (s.defaultColor !== prev.defaultColor) applyDefaultColor(scene);
        if (s.background !== prev.background || s.defaultColor !== prev.defaultColor) requestRender();
    });
}
