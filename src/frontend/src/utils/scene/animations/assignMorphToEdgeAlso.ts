import * as THREE from "three";

export function assignMorphToEdgeAlso(
    mesh: THREE.Mesh,
    edges: THREE.LineSegments
): void {
    const meshGeom = mesh.geometry as THREE.BufferGeometry;
    const meshPosAttr = meshGeom.attributes.position as THREE.BufferAttribute;
    const meshMorphs = meshGeom.morphAttributes.position!;
    const meshRel = meshGeom.morphTargetsRelative;
    const meshInf = mesh.morphTargetInfluences!;
    const meshDict = mesh.morphTargetDictionary!;

    const lineGeom = edges.geometry as THREE.BufferGeometry;
    const linePosAttr = lineGeom.attributes.position as THREE.BufferAttribute;
    let indexAttr = lineGeom.index;

    // 1) if there's no index → build one by matching each line-vertex
    if (!indexAttr) {
        // build a map: "x_y_z" → mesh-vertex-index
        const meshArr = meshPosAttr.array as Float32Array;
        const meshCount = meshArr.length / 3;
        const posMap = new Map<string, number>();
        for (let i = 0; i < meshCount; i++) {
            const key = `${meshArr[3 * i]}_${meshArr[3 * i + 1]}_${meshArr[3 * i + 2]}`;
            posMap.set(key, i);
        }

        // now map each line-vertex into that same index
        const lineArr = linePosAttr.array as Float32Array;
        const lineCount = lineArr.length / 3;
        const idx = new (meshCount > 0xffff ? Uint32Array : Uint16Array)(lineCount);
        for (let i = 0; i < lineCount; i++) {
            const key = `${lineArr[3 * i]}_${lineArr[3 * i + 1]}_${lineArr[3 * i + 2]}`;
            const mi = posMap.get(key);
            if (mi === undefined) {
                throw new Error(
                    `Cannot infer index for line-vertex ${i}: no matching mesh-vertex found`
                );
            }
            idx[i] = mi;
        }

        indexAttr = new THREE.BufferAttribute(idx, 1);
    }

    // 2) swap in the mesh’s position & morph buffers
    lineGeom.setAttribute('position', meshPosAttr);
    lineGeom.morphAttributes.position = meshMorphs;
    lineGeom.morphTargetsRelative = meshRel;
    lineGeom.setIndex(indexAttr);

    // 3) share the same influences & dictionary
    edges.morphTargetInfluences = meshInf;
    edges.morphTargetDictionary = meshDict;

    // 4) turn on morphTargets in the material
    const mat = edges.material as any;      // TS needs a little help here
    mat.morphTargets = true;
    mat.needsUpdate = true;

    // 5) flag the position attribute as updated
    meshPosAttr.needsUpdate = true;
}