// FEA streaming: element edges follow the elements they bound.
//
// Owns: hiding a wireframe edge once every element it borders is hidden, and showing it
// again with any of them. Without this, hiding elements left their outline floating in
// the air. An edge is owned by the elements whose triangles contain both its endpoints;
// an edge no element owns (a beam drawn only as a line) is left alone.
//
// Hidden edges become zero-length pairs in the overlay's own index buffer, so the buffer
// keeps its size and every renderer simply draws nothing for them.
//
// Inputs: `useVisibilityStore` (bumped by every draw-range hide/show), the scene's FEA
// meshes with their edge overlays, and group isolation's wireframe-ghost choice.

import * as THREE from "three";
import {useVisibilityStore} from "@/state/visibilityStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {edgeOwners, maskHiddenEdges, type EdgeOwners} from "../feaEdgeFilter";

const OVERLAYS = ["fea-element-edges", "fea-beam-element-edges"];

interface EdgeState {
    all: Uint32Array; // the overlay's full edge list, before any masking
    owners: EdgeOwners | null; // built on the first hide
}

// Group isolation's "show the rest as wireframe": hidden elements keep their outline as a ghost.
let ghostHidden = false;

/** Keep hidden elements' edges drawn (a wireframe ghost of what is hidden), or not. */
export function setHiddenEdgesGhosted(on: boolean): void {
    ghostHidden = on;
}

/** Bring every FEA element-edge overlay in the scene in line with its mesh's hidden ranges. */
export function syncFeaEdgeVisibility(): void {
    const scene = getViewerRuntime().scene.current;
    if (!scene) return;
    scene.traverse((overlay) => {
        if (!(overlay instanceof THREE.LineSegments) || !OVERLAYS.includes(overlay.name)) return;
        const mesh = overlay.parent;
        const edgeIndex = overlay.geometry.getIndex();
        const triIndex = mesh instanceof CustomBatchedMesh ? mesh.geometry.getIndex() : null;
        if (!(mesh instanceof CustomBatchedMesh) || !edgeIndex || !triIndex) return;
        let state = overlay.userData.edgeVisibility as EdgeState | undefined;
        if (!state) {
            state = {all: Uint32Array.from(edgeIndex.array as ArrayLike<number>), owners: null};
            overlay.userData.edgeVisibility = state;
        }
        const out = edgeIndex.array as Uint32Array;
        if (ghostHidden || mesh.getHiddenRanges().size === 0) {
            out.set(state.all);
        } else {
            state.owners ??= edgeOwners(mesh.drawRanges, triIndex.array, mesh.geometry.attributes.position.count, state.all);
            maskHiddenEdges(state.all, state.owners, (id) => mesh.isRangeHidden(id), out);
        }
        edgeIndex.needsUpdate = true;
    });
}

let subscribed = false;

/** Follow draw-range visibility from now on. Idempotent. */
export function followEdgeVisibility(): void {
    if (subscribed) return;
    subscribed = true;
    useVisibilityStore.subscribe(syncFeaEdgeVisibility);
}
