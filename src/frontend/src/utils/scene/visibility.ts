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
import {getViewerRuntime} from "@/state/viewerRuntime";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";

/** Hide every draw range currently selected. No-op when nothing is
 * selected. Walks past wrapper Object3Ds the same way the existing
 * Shift+H handler does — selection entries can be keyed by either a
 * CustomBatchedMesh or a wrapper whose subtree contains one. */
export function hideSelectedRanges(): void {
    const store = useSelectedObjectStore.getState();
    const selected = store.selectedObjects;
    if (selected.size === 0) return;
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
    // The selection's blue overlay draws on top of the base geometry, so hiding
    // the base alone leaves the highlight rendering the now-hidden ranges in blue
    // until the selection is cleared (the "still there until I click elsewhere"
    // nuisance). Clear the selection now so the highlight vanishes with the
    // geometry; the ranges stay hidden (hiddenRanges is independent of the
    // selection). Also drop the stale name so the panel doesn't offer Hide on an
    // already-hidden object.
    store.clearSelectedObjects();
    useObjectInfoStore.getState().setName(null);
    // On-demand render loop: hide mutates per-draw-range material flags
    // which the renderer doesn't observe on its own.
    requestRender();
}

/** Hide everything that is NOT selected, across every loaded mesh -- the inverse of
 * ``hideSelectedRanges`` (Shift+I). The selection is kept, so the user can act on what is left on
 * screen; Shift+U brings the rest back.
 *
 * A no-op when nothing is selected: "hide everything" is not what anybody pressing this means.
 * Ranges hidden before stay hidden -- a selected range that was already hidden is not resurrected
 * by hiding the others. */
export function hideUnselectedRanges(): void {
    const selected = useSelectedObjectStore.getState().selectedObjects;
    if (selected.size === 0) return;
    const scene = getViewerRuntime().scene.current;
    if (!scene) return;

    // Selection entries can be keyed by a CustomBatchedMesh or by a wrapper whose subtree holds
    // one (see hideSelectedRanges); fold both into "ranges to keep, per mesh".
    const keep = new Map<CustomBatchedMesh, Set<string>>();
    const addKeep = (mesh: CustomBatchedMesh, ids: Iterable<string>) => {
        const set = keep.get(mesh) ?? new Set<string>();
        for (const id of ids) set.add(id);
        keep.set(mesh, set);
    };
    selected.forEach((rangeIds, obj) => {
        if (obj instanceof CustomBatchedMesh) addKeep(obj, rangeIds);
        else (obj as THREE.Object3D).traverse((child) => {
            if (child instanceof CustomBatchedMesh) addKeep(child, rangeIds);
        });
    });

    scene.traverse((obj) => {
        if (!(obj instanceof CustomBatchedMesh)) return;
        const hidden = obj.getHiddenRanges();
        const visibleSelected = new Set([...(keep.get(obj) ?? [])].filter((id) => !hidden.has(id)));
        // A mesh with nothing selected is isolated to nothing, i.e. hidden whole.
        obj.isolateDrawRanges(visibleSelected);
    });
    requestRender();
}

/** Unhide every draw range across every loaded mesh. No-op when
 * nothing is hidden — safe to call unconditionally. */
export function unhideAllRanges(): void {
    const scene = getViewerRuntime().scene.current;
    if (!scene) return;
    scene.traverse((obj) => {
        if (obj instanceof CustomBatchedMesh) {
            obj.unhideAllDrawRanges();
        }
    });
    requestRender();
}
