// Which of the renderer's instances a node of a loaded file is, and where each goes when the node is
// placed: the arithmetic of the three.js-free viewport's `modelNodes` (NativeScene), apart from the
// renderer so that it can be checked without one.
//
// A node is the instances that draw it and those that draw what hangs on it in the file. Each such
// instance keeps its place relative to the node. The renderer draws in coordinates of its own (the
// scene's recentring); where an instance was put at rest says where the file's coordinates are in
// those, so nothing here needs to be told.
//
// No three.js in this module.
import type {PosableGlb} from "@/utils/glb/glbPosable";
import {invert4, multiply4} from "@/viewport/api/mat4";
import {POSE_MATRIX_SIZE} from "@/viewport/api/modelPose";

export interface DrivenInstance {
    instance: number;
    /** Which of the names places it. */
    index: number;
    /** The file's coordinates in the renderer's. */
    frame: Float64Array;
    /** The instance's node in the frame of the node that is placed. */
    relative: Float64Array;
}

export interface NodePlacement {
    /** Per name: whether the file has a node of that name that can be placed. */
    found: boolean[];
    driven: DrivenInstance[];
}

/**
 * `names` against the nodes of `glb`. `instances` are the renderer's, as (instance, node of the
 * file) pairs; `restMatrix` answers where the renderer has an instance now, which must be at rest
 * (sixteen numbers, column-major).
 */
export function planNodePlacement(
    glb: PosableGlb,
    names: readonly string[],
    instances: readonly number[],
    restMatrix: (instance: number) => ArrayLike<number>,
): NodePlacement {
    const indexOfName = new Map<string, number>();
    names.forEach((name, index) => {
        if (!indexOfName.has(name)) indexOfName.set(name, index);
    });
    // The node each name places: the first of that name. And per node, which name places it: its
    // own, else its nearest ancestor's.
    const nodeOf = new Int32Array(names.length).fill(-1);
    const nameAt = new Int32Array(glb.names.length).fill(-1);
    glb.names.forEach((name, node) => {
        const index = name ? indexOfName.get(name) : undefined;
        if (index === undefined || nodeOf[index] >= 0) return;
        nodeOf[index] = node;
        nameAt[node] = index;
    });
    const placedBy = (start: number): number => {
        for (let node = start, steps = 0; node >= 0 && steps <= glb.names.length; node = glb.parents[node], steps++) {
            if (nameAt[node] >= 0) return nameAt[node];
        }
        return -1;
    };
    const found = names.map((_, index) => nodeOf[index] >= 0 && glb.apart[nodeOf[index]]);
    const driven: DrivenInstance[] = [];
    const inverse = new Float64Array(16);
    for (let i = 0; i + 1 < instances.length; i += 2) {
        const instance = instances[i];
        const node = instances[i + 1];
        if (node < 0 || node >= glb.names.length) continue;
        const index = placedBy(node);
        if (index < 0) continue;
        const frame = new Float64Array(16);
        const relative = new Float64Array(16);
        if (!invert4(inverse, glb.rest, node * 16)) continue;
        multiply4(frame, restMatrix(instance), inverse);
        if (!invert4(inverse, glb.rest, nodeOf[index] * 16)) continue;
        multiply4(relative, inverse, glb.rest, 0, node * 16);
        driven.push({instance, index, frame, relative});
    }
    return {found, driven};
}

/** Where `driven` goes for `matrices` (`POSE_MATRIX_SIZE` numbers per name), into `out`. False when
 *  `matrices` has nothing for it. A matrix of zeros gives zeros: the instance is drawn with no size. */
export function placeInstance(driven: DrivenInstance, matrices: ArrayLike<number>, out: Float64Array): boolean {
    const at = driven.index * POSE_MATRIX_SIZE;
    if (at + POSE_MATRIX_SIZE > matrices.length) return false;
    if (matrices[at + 15] === 0) {
        out.fill(0);
        return true;
    }
    multiply4(out, driven.frame, matrices, 0, at);
    multiply4(out, out, driven.relative);
    return true;
}
