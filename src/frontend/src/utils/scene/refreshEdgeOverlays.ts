import * as THREE from "three";
import {sceneRef, rendererRef} from "@/state/refs";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {useOptionsStore} from "@/state/optionsStore";
import {requestRender} from "@/state/perfStore";

// Rebuild every design mesh's edge overlay in place — WITHOUT a page/model reload — so a change to
// `showEdges` (on/off) or `hideTessellationEdges` (feature-only vs full triangulation grid) takes
// effect immediately. Both are otherwise baked at model load (prepareLoadedModel builds the overlay
// once, reading hideTessellationEdges at that time), which is why the options drawer says "reload".
//
// For each eligible mesh (one that took an edge overlay at load — FEA streaming meshes never do): drop
// the stale overlay + its cached geometry, and when edges are on, re-add a freshly-built overlay
// (rebuilt from the CURRENT options). Preserves the overlay's layer (1) + parent so picking/masking is
// unchanged. NB: a live rebuild resets the edge material's per-range highlight/hide state — re-click to
// restore it if needed.
export function refreshEdgeOverlays(): void {
    const scene = sceneRef.current;
    const renderer = rendererRef.current;
    if (!scene || !renderer) return;
    const showEdges = useOptionsStore.getState().showEdges;
    const meshes: CustomBatchedMesh[] = [];
    scene.traverse((o) => {
        if (o instanceof CustomBatchedMesh && o.edgesEligible) meshes.push(o);
    });
    for (const mesh of meshes) {
        const old = mesh.invalidateEdgeOverlay();
        const parent = old?.parent ?? (mesh.parent as THREE.Object3D | null);
        old?.parent?.remove(old);
        if (showEdges && parent && mesh.drawRanges.size) parent.add(mesh.getEdgeOverlay(renderer));
    }
    requestRender();
}
