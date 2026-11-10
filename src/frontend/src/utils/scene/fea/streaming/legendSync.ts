// FEA streaming: the result session of the three.js viewport, as the controls
// and the legend see it.
//
// Owns: naming the painted three.js mesh by its result layer and its size, and
// handing it to `syncResultStores` (./resultStores), which registers the
// session with the animation store and drives the legend for either viewport.
//
// Inputs: the painted mesh and its base positions, and what `syncResultStores`
// takes.

import type * as THREE from "three";

import {threeResultLayer} from "../threeResultLayer";
import {syncResultStores} from "./resultStores";

export function syncResultSession(
    args: Omit<Parameters<typeof syncResultStores>[0], "layer" | "mesh" | "modelSize"> & {
        mesh: THREE.Mesh;
        /** The mesh's undeformed positions, in source numbering. */
        basePositions: Float32Array;
    },
): void {
    const {mesh, basePositions, ...rest} = args;
    syncResultStores({
        ...rest,
        mesh,
        layer: threeResultLayer(mesh, basePositions),
        modelSize: () => {
            const geom = mesh.geometry;
            // Recompute rather than trust a cached box: a stale one from an
            // earlier state made the derived scale wobble between field
            // switches.
            geom.computeBoundingBox();
            return geom.boundingBox ? geom.boundingBox.min.distanceTo(geom.boundingBox.max) : 0;
        },
    });
}
