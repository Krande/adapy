// The selection highlight overlay of a `CustomBatchedMesh`: its geometry, a new geometry that
// REFERENCES the base mesh's index, attributes and morph attributes rather than copying them, so
// it costs no extra memory and the GPU morphs it with the base mesh.

import * as THREE from "three";

import { followMorph } from "@/utils/scene/fea/gpuRefresh";

export function sharedOverlayGeometry(srcGeom: THREE.BufferGeometry): THREE.BufferGeometry {
    const overlayGeom = new THREE.BufferGeometry();
    // Reference base attributes/index (do NOT clone to avoid extra memory and to leverage GPU morphs)
    if (srcGeom.index) overlayGeom.setIndex(srcGeom.index);
    for (const name of Object.keys(srcGeom.attributes)) {
        overlayGeom.setAttribute(name, srcGeom.getAttribute(name));
    }
    // Morph attributes
    const morphs: Record<string, (THREE.BufferAttribute | THREE.InterleavedBufferAttribute)[]> = {};
    if (srcGeom.morphAttributes) {
        for (const mName of Object.keys(srcGeom.morphAttributes)) {
            morphs[mName] = (srcGeom.morphAttributes as typeof morphs)[mName];
        }
    }
    overlayGeom.morphAttributes = morphs as THREE.BufferGeometry["morphAttributes"];
    overlayGeom.morphTargetsRelative = srcGeom.morphTargetsRelative === true;
    followMorph(srcGeom, overlayGeom);
    return overlayGeom;
}

/** The overlay's material: morphed by the GPU with the base mesh, both sides drawn, and pulled
 *  towards the camera so it does not z-fight the surface it highlights. */
export function prepareOverlayMaterial(mat: THREE.Material): void {
    (mat as THREE.Material & {morphTargets?: boolean}).morphTargets = true; // let GPU handle morphs
    mat.side = THREE.DoubleSide;
    mat.polygonOffset = true;
    mat.polygonOffsetFactor = -1;
    mat.polygonOffsetUnits = -1;
}
