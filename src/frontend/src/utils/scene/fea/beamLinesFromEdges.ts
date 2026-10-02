import * as THREE from "three";

import {installResultLineSegments} from "./resultLineSegments";

/** The element-edge wireframe `assembleFeaGlb` hangs under the FEA primitive. */
export const FEA_EDGE_LINES_NAME = "fea-element-edges";

/**
 * Draw a beam model's elements as fat, result-coloured lines that follow the
 * mode-shape morph.
 *
 * A beam bundle's mesh is points only -- one per node, there are no faces --
 * so the only thing drawing the elements is the element-edge wireframe: a
 * one-pixel grey hairline that is barely visible and carries no colour. The
 * standalone viewer draws beams with `installResultLineSegments`; this builds
 * the same input from what the bundle has: the edge index gives the segments,
 * the points' colour and morph attributes give each end's colour and
 * displacement. Those lines deform on the CPU from `points`'
 * `morphTargetInfluences[0]`, the number the animation driver writes.
 *
 * Returns false (and changes nothing) when `points` has no edge child, or the
 * colour or morph attribute is missing. The hairline is hidden once the fat
 * lines are in, since they draw the same segments.
 */
export function installBeamLinesFromEdges(points: THREE.Object3D & {geometry: THREE.BufferGeometry}): boolean {
    const edges = points.getObjectByName(FEA_EDGE_LINES_NAME) as THREE.LineSegments | undefined;
    const index = edges?.geometry.getIndex();
    const position = points.geometry.getAttribute("position");
    const color = points.geometry.getAttribute("color");
    const morph = points.geometry.morphAttributes.position?.[0];
    if (!edges || !index || !position || !color || !morph) return false;

    const nSegments = Math.floor(index.count / 2);
    const positions = new Float32Array(nSegments * 6);
    const colors = new Float32Array(nSegments * 6);
    const displacement = new Float32Array(nSegments * 6);
    for (let s = 0; s < nSegments; s++) {
        for (let end = 0; end < 2; end++) {
            const v = index.getX(s * 2 + end);
            const o = s * 6 + end * 3;
            positions[o] = position.getX(v);
            positions[o + 1] = position.getY(v);
            positions[o + 2] = position.getZ(v);
            colors[o] = color.getX(v);
            colors[o + 1] = color.getY(v);
            colors[o + 2] = color.getZ(v);
            displacement[o] = morph.getX(v);
            displacement[o + 1] = morph.getY(v);
            displacement[o + 2] = morph.getZ(v);
        }
    }
    installResultLineSegments(points as unknown as THREE.Mesh, positions, colors, displacement);
    edges.visible = false;
    return true;
}
