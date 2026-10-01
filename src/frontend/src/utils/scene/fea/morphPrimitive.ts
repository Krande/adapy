import * as THREE from "three";

/** A primitive carrying the FEA mode-shape morph. */
export type MorphPrimitive = THREE.Object3D & {
    geometry: THREE.BufferGeometry;
    material: THREE.Material | THREE.Material[];
    morphTargetInfluences?: number[];
};

/**
 * The first primitive under `root` whose geometry carries a position
 * morph -- the mode displacement `assembleFeaGlb` installs. A model with
 * faces carries it on a Mesh (CustomBatchedMesh is one too); a beam model
 * has none, and `assembleFeaGlb` hangs it on a LineSegments (or Points)
 * primitive instead, so those count as well. Looking for a Mesh only left
 * every beam case undeformed and without the simulation controls.
 *
 * `traverse` visits a parent before its children, so the wireframe-edge
 * LineSegments that shares the primitive's morph is never picked over it.
 */
export function findMorphPrimitive(root: THREE.Object3D): MorphPrimitive | null {
    let found: MorphPrimitive | null = null;
    root.traverse((o) => {
        if (found) return;
        const obj = o as any;
        if ((obj.isMesh || obj.isLine || obj.isPoints) && obj.geometry?.morphAttributes?.position?.length > 0) {
            found = obj;
        }
    });
    return found;
}
