/**
 * Scene-level visibility actions for the currently-loaded model(s).
 *
 * "Visibility" here is the per-draw-range hide bit on
 * ``CustomBatchedMesh`` — distinct from "loaded into the scene"
 * (which is the storage-browser checkbox). Hidden geometry stays in
 * memory and in the scene graph; it just renders with the hidden
 * material. Unloading via ``clear_loaded_model`` or
 * ``unload_source_from_scene`` is a different operation that
 * removes the mesh entirely.
 *
 * These helpers are the single source of truth for the hide / unhide
 * behaviour: the Shift+H / Shift+U keyboard shortcuts and the
 * Selected Object Info panel buttons both call them, so kbd and tap
 * paths stay symmetric.
 */

import * as THREE from "three";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {sceneRef} from "@/state/refs";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";

/** Hide every draw range currently selected. No-op when nothing is
 * selected. Walks past wrapper Object3Ds the same way the existing
 * Shift+H handler does — selection entries can be keyed by either a
 * CustomBatchedMesh or a wrapper whose subtree contains one. */
export function hideSelectedRanges(): void {
    const selected = useSelectedObjectStore.getState().selectedObjects;
    selected.forEach((rangeIds, mesh) => {
        if (mesh instanceof CustomBatchedMesh) {
            mesh.hideBatchDrawRange(rangeIds);
            return;
        }
        (mesh as THREE.Object3D).traverse((child: THREE.Object3D) => {
            if (child instanceof CustomBatchedMesh) {
                child.hideBatchDrawRange(rangeIds);
            }
        });
    });
}

/** Unhide every draw range across every loaded mesh. No-op when
 * nothing is hidden — safe to call unconditionally. */
export function unhideAllRanges(): void {
    const scene = sceneRef.current;
    if (!scene) return;
    scene.traverse((obj) => {
        if (obj instanceof CustomBatchedMesh) {
            obj.unhideAllDrawRanges();
        }
    });
}
