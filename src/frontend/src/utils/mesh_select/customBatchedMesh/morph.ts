// Morph-target deformation of one vertex on the CPU, the way three.js applies it on the GPU:
// relative targets add their offset times the influence; absolute targets blend the position
// towards the target. Shared by `CustomBatchedMesh`'s raycast and its CPU overlay updater.

import type * as THREE from "three";

/** Morph `target` (holding vertex `idx`'s base position) by the active influences, in place. */
export function applyMorphTargets(
    target: THREE.Vector3,
    idx: number,
    morphPositions: readonly THREE.BufferAttribute[] | undefined,
    influences: readonly number[] | undefined,
    relative: boolean,
): void {
    if (!morphPositions || !influences) return;
    let sumInfluence = 0;
    for (let i = 0; i < morphPositions.length; i++) {
        const inf = influences[i] || 0;
        if (inf === 0) continue;
        sumInfluence += inf;
        const mp = morphPositions[i];
        const mx = mp.getX(idx);
        const my = mp.getY(idx);
        const mz = mp.getZ(idx);
        if (relative) {
            target.x += mx * inf;
            target.y += my * inf;
            target.z += mz * inf;
        } else {
            // absolute morph targets: blend base towards target
            target.x = target.x * (1 - sumInfluence) + mx * inf;
            target.y = target.y * (1 - sumInfluence) + my * inf;
            target.z = target.z * (1 - sumInfluence) + mz * inf;
        }
    }
}
