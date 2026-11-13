// The three.js viewport's adapter for placing a loaded model's nodes (./modelPose.ts): a node is an
// object of the loaded scene, found by the name the file gives it, and placing it is setting that
// object's matrix. What hangs on it in the file hangs on it in the scene, and moves with it.
import {Matrix4, type Object3D, type Scene} from "three";

import {loadedSourceGroups} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {sourceSpaceRoot} from "@/utils/scene/meshLookup";
import {POSE_MATRIX_SIZE, type ModelNodePoser} from "./modelPose";

/** The name a glTF node has in its file. The loader keeps it in `userData.name` and gives the
 *  object itself a name it has made safe and unique, which is the file's when it already was. */
const fileName = (object: Object3D): string => (object.userData?.name as string | undefined) ?? object.name;

function inScene(object: Object3D): boolean {
    let top = object;
    while (top.parent) top = top.parent;
    return (top as Scene).isScene === true;
}

/**
 * The nodes called `names` under `root`, whose own space is the file's coordinates. Where a name is
 * given to more than one node the first one met from the root down is taken.
 */
export function posedNodesUnder(root: Object3D, names: readonly string[], alive: () => boolean = () => inScene(root)): ModelNodePoser {
    const wanted = new Map<string, number>();
    names.forEach((name, index) => {
        if (!wanted.has(name)) wanted.set(name, index);
    });
    const nodes: (Object3D | null)[] = names.map(() => null);
    root.traverse((object) => {
        if (object === root) return;
        const index = wanted.get(fileName(object));
        if (index !== undefined && !nodes[index]) nodes[index] = object;
    });
    // Placed from the root down: a node that hangs on another that is placed too is put in the
    // file's coordinates after the one it hangs on has moved, whatever order they were named in.
    const depth = (object: Object3D): number => {
        let n = 0;
        for (let up = object.parent; up && up !== root; up = up.parent) n++;
        return n;
    };
    const order = nodes
        .map((node, index) => ({index, depth: node ? depth(node) : 0}))
        .filter(({index}) => nodes[index] !== null)
        .sort((a, b) => a.depth - b.depth)
        .map(({index}) => index);
    const matrix = new Matrix4();
    const rootInverse = new Matrix4();
    const parentInverse = new Matrix4();
    return {
        found: nodes.map((node) => node !== null),
        alive,
        place(matrices) {
            root.updateWorldMatrix(true, false);
            rootInverse.copy(root.matrixWorld).invert();
            for (let k = 0; k < order.length; k++) {
                const node = nodes[order[k]]!;
                const at = order[k] * POSE_MATRIX_SIZE;
                if (at + POSE_MATRIX_SIZE > matrices.length) continue;
                // A matrix of zeros: not drawn. (One with no size has no inverse to light it by.)
                if (matrices[at + 15] === 0) {
                    node.visible = false;
                    continue;
                }
                node.visible = true;
                matrix.fromArray(matrices, at);
                const parent = node.parent;
                if (parent && parent !== root) {
                    // The matrix is in the file's coordinates; the object's own is in its parent's.
                    parent.updateWorldMatrix(true, false);
                    parentInverse.multiplyMatrices(rootInverse, parent.matrixWorld).invert();
                    matrix.premultiply(parentInverse);
                }
                node.matrixAutoUpdate = false;
                node.matrix.copy(matrix);
                node.matrixWorldNeedsUpdate = true;
            }
            requestRender();
        },
    };
}

/** `ViewportApi.modelNodes` on the three.js viewport. */
export function threeModelNodes(sourceName: string, names: readonly string[]): ModelNodePoser | null {
    const group = loadedSourceGroups.get(sourceName);
    if (!group) return null;
    // The loaded scene itself: the object that carries the file's coordinates into the world (its
    // recentring and, for a y-up file, its turn upright).
    return posedNodesUnder(sourceSpaceRoot(group), names);
}
