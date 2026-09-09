import * as THREE from "three";

import {FEA_UNDEFORMED_COLOR} from "./edgeColors";

// The undeformed shape, drawn behind the deformed one.
//
// A warped model tells you where things ended up and not where they started, and
// at any exaggeration worth using the two are far apart. Every FE post-processor
// offers the original outline as a reference for exactly that reason.
//
// It is a SEPARATE object holding its own copy of the base positions, rather than
// the same geometry rendered twice: the live geometry's position buffer is
// rewritten on every field apply, and an element field swaps it wholesale for an
// element-local expansion. Sharing it would make the reference follow the thing
// it is supposed to be a reference for.

const GHOST_NAME = "__fea_undeformed_ghost__";

/** Dim enough to read as background, bright enough to trace an edge against. */
const GHOST_COLOR = FEA_UNDEFORMED_COLOR;

export function clearUndeformedGhost(mesh: THREE.Object3D): void {
    const existing = mesh.getObjectByName(GHOST_NAME) as THREE.LineSegments | undefined;
    if (!existing) return;
    existing.removeFromParent();
    existing.geometry.dispose();
    (existing.material as THREE.Material).dispose();
}

/**
 * Draw `basePositions` as a static wireframe under `mesh`.
 *
 * `edgeIndices` is the bake's element-edge sidecar — the same index the live
 * wireframe uses, in source vertex numbering, so the ghost shows element
 * boundaries rather than the triangulation's diagonals.
 *
 * No morph attribute at all: that is the whole point, and it also means the
 * ghost costs nothing per frame.
 */
export function installUndeformedGhost(
    mesh: THREE.Object3D,
    basePositions: Float32Array,
    edgeIndices: Uint32Array,
): void {
    clearUndeformedGhost(mesh);
    if (basePositions.length === 0 || edgeIndices.length === 0) return;

    const geometry = new THREE.BufferGeometry();
    // A copy, not a view: the caller's array is the live base snapshot and is
    // rewritten in place when the source reloads.
    geometry.setAttribute("position", new THREE.BufferAttribute(basePositions.slice(), 3));
    geometry.setIndex(new THREE.BufferAttribute(edgeIndices.slice(), 1));

    const material = new THREE.LineBasicMaterial({
        color: GHOST_COLOR,
        transparent: true,
        opacity: 0.45,
        // Behind the deformed model rather than through it: the reference is
        // context, and one that draws over the result competes with it.
        depthTest: true,
    });

    const ghost = new THREE.LineSegments(geometry, material);
    ghost.name = GHOST_NAME;
    // Below the element edges (3) and the result lines (4) — it is the backdrop.
    ghost.renderOrder = 1;
    ghost.frustumCulled = false;
    // Layer 1: drawn, never picked. Clicking the shape a result USED to have is
    // not a selection anyone means to make.
    ghost.layers.set(1);
    mesh.add(ghost);
}

/** Whether the ghost is currently installed under `mesh`. */
export function hasUndeformedGhost(mesh: THREE.Object3D): boolean {
    return mesh.getObjectByName(GHOST_NAME) != null;
}
