import * as THREE from "three";

// Make a THREE.Points follow the same morph target deformation as a source mesh.
// Reuses the mesh's position buffer, morph targets, and the morphTarget influences/dictionary.
export function assignMorphToPointsAlso(
    mesh: THREE.Mesh,
    points: THREE.Points
): void {
    const meshGeom = mesh.geometry as THREE.BufferGeometry;
    const meshPosAttr = meshGeom.attributes.position as THREE.BufferAttribute;
    const meshMorphs = meshGeom.morphAttributes.position!;
    const meshRel = meshGeom.morphTargetsRelative;
    const meshInf = mesh.morphTargetInfluences!;
    const meshDict = mesh.morphTargetDictionary!;

    const ptsGeom = points.geometry as THREE.BufferGeometry;

    // Use the mesh position attribute directly so GPU updates apply consistently
    ptsGeom.setAttribute('position', meshPosAttr);
    ptsGeom.morphAttributes.position = meshMorphs;
    ptsGeom.morphTargetsRelative = meshRel;

    // Share influences and dictionary so animation system drives both
    (points as any).morphTargetInfluences = meshInf;
    (points as any).morphTargetDictionary = meshDict;

    // Ensure material supports morph targets
    const mat = points.material as any;
    if (mat) {
        mat.morphTargets = true;
        mat.needsUpdate = true;
    }

    // Flag update
    meshPosAttr.needsUpdate = true;
}
