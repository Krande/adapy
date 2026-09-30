// Remove a single previously-overlaid source from the scene without
// touching the rest. Counterpart to overlay_file_in_scene; called
// when the user unchecks a file in the StorageBrowser.

import {Object3D} from "three";
import {useModelState} from "@/state/modelState";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {requestRender} from "@/state/perfStore";
import {disposeObject3D} from "@/utils/scene/dispose_object";

export function unload_source_from_scene(sourceName: string): void {
    const group = useModelState.getState().unregisterLoadedSource(sourceName);

    // Drop this model's root from the tree view + the model-key map.
    // Each load registers one tree root under the synthetic container
    // (cacheAndBuildTree) keyed by model_key = the runtime modelKeyMap key
    // for this group; without this the hierarchy panel keeps showing
    // the unloaded model and selection-sync walks dead refs.
    let modelKey: string | null = null;
    if (group) {
        getViewerRuntime().modelKeyMap.current?.forEach((g, key) => {
            if (g === group) modelKey = key;
        });
    }
    if (modelKey !== null) getViewerRuntime().modelKeyMap.current?.delete(modelKey);
    // The root goes by EITHER handle: the model key, when the group was found in the key map, or
    // the source name the root was stamped with at load. Keyed on the model key alone, a model
    // whose lookup came back empty left its rows behind -- a tree showing a model the scene no
    // longer holds. Done before the group check for the same reason: a stale row is removed even
    // when there is no group left to remove.
    const isThisModel = (n: {model_key?: string | null; source_name?: string | null}) =>
        (modelKey !== null && n.model_key === modelKey) || (!!n.source_name && n.source_name === sourceName);
    const ts = useTreeViewStore.getState();
    const td = ts.treeData;
    if (td) {
        if (isThisModel(td)) {
            // Single un-containered root (first/only model).
            ts.clearTreeData();
        } else if (Array.isArray(td.children)) {
            const remaining = td.children.filter((c) => !isThisModel(c));
            if (remaining.length !== td.children.length) {
                if (remaining.length === 0) ts.clearTreeData();
                else ts.setTreeData({...td, children: remaining});
            }
        }
    }
    if (!group) return;

    // Drop selection entries that point at meshes we're about to
    // detach, BEFORE we tear the group down. Without this, the
    // useSelectedObjectStore map keeps live keys to garbage-collected
    // mesh instances; subsequent reloads (Show all after a partial
    // unload, etc.) end up with a "N selected" count whose visual
    // highlight is gone, and the clipboard / repaint helpers walk
    // dead refs.
    const owned = new Set<unknown>();
    (group as Object3D).traverse((child: Object3D) => {
        owned.add(child);
        if (child instanceof CustomBatchedMesh) owned.add(child);
    });
    owned.add(group);
    const sel = useSelectedObjectStore.getState();
    sel.selectedObjects.forEach((rangeIds, mesh) => {
        if (!owned.has(mesh)) return;
        for (const id of rangeIds) sel.removeSelectedObject(mesh, id);
    });

    // Mirror what clear_loaded_model does per-group: explicitly dispose the GPU resources
    // (three.js does NOT free them on detach — clear()/remove() only drop references), then
    // detach the children + remove from the parent scene.
    disposeObject3D(group);
    group.clear();
    getViewerRuntime().scene.current?.remove(group);
    // On-demand render loop won't tick until the next OrbitControls
    // 'change' event — without this kick the just-removed group
    // keeps rendering on the canvas until the user rotates.
    requestRender();
}
