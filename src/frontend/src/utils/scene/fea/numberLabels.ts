// Number labels over a loaded FEA result, for a result drawn by a three.js mesh: the mesh as a
// label source (its draw ranges, its index, each vertex where the morph puts it now) and the
// camera as a projector. What is worth a label, and where it goes, is decided by
// `labelPlacement.ts`, which the three.js-free viewport uses with a source of its own.

import * as THREE from "three";

import {elementLocalMap} from "./elementLocalGeometry";
import {vertexWorldPosition} from "./goToNode";
import {matrixProjector, placeLabels, type LabelPlacementOptions, type LabelSource, type ScreenLabel} from "./labelPlacement";

export {elementText, type LabelKind, type ScreenLabel} from "./labelPlacement";

export interface LabelOptions extends LabelPlacementOptions {
    /** Size of the view in CSS pixels. */
    width: number;
    height: number;
}

interface MeshLike extends THREE.Mesh {
    drawRanges?: Map<string, [number, number]>;
    isRangeHidden?: (id: string) => boolean;
}

/** `mesh` as a label source; null when it has no elements to label. Positions follow the
 *  deformed shape (the morph state of the mesh), through the same lookup "go to node" uses. */
export function threeLabelSource(mesh: THREE.Mesh | null): LabelSource | null {
    const m = mesh as MeshLike | null;
    const index = m?.geometry.getIndex();
    const ranges = m?.drawRanges;
    if (!m || !index || !ranges) return null;
    // After an element field the mesh has a vertex per triangle corner: a node is then many
    // vertices, and is labelled once, by its own number.
    const nodes = elementLocalMap(m.geometry);
    return {
        ranges,
        index: index.array,
        nodeOf: nodes ? (v) => nodes[v] : undefined,
        position: (v, out, at) => {
            const p = vertexWorldPosition(m, v);
            if (!p) return false;
            out[at] = p.x;
            out[at + 1] = p.y;
            out[at + 2] = p.z;
            return true;
        },
        isHidden: m.isRangeHidden ? (key) => m.isRangeHidden!(key) : undefined,
    };
}

/** The labels for `mesh` seen from `camera`. */
export function collectLabels(mesh: THREE.Mesh | null, camera: THREE.Camera | null, opts: LabelOptions): ScreenLabel[] {
    if (!mesh || !camera || (!opts.nodes && !opts.elements && !opts.values) || opts.width <= 0 || opts.height <= 0) return [];
    const project = matrixProjector(camera.matrixWorldInverse.elements, camera.projectionMatrix.elements, opts.width, opts.height, -1);
    return placeLabels(threeLabelSource(mesh), project, opts);
}
