// Remove a single previously-overlaid source from the scene without
// touching the rest. Counterpart to overlay_file_in_scene; called
// when the user unchecks a file in the StorageBrowser.

import {Object3D} from "three";
import {useModelState} from "@/state/modelState";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {modelKeyMapRef, sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {requestRender} from "@/state/perfStore";

export function unload_source_from_scene(sourceName: string): void {
    const group = useModelState.getState().unregisterLoadedSource(sourceName);
    if (!group) return;

    // Drop this model's root from the tree view + the model-key map.
    // Each load registers one tree root under the synthetic container
    // (cacheAndBuildTree) keyed by model_key = the modelKeyMapRef key
    // for this group; without this the hierarchy panel keeps showing
    // the unloaded model and selection-sync walks dead refs.
    let modelKey: string | null = null;
    modelKeyMapRef.current?.forEach((g, key) => {
        if (g === group) modelKey = key;
    });
    if (modelKey !== null) {
        modelKeyMapRef.current?.delete(modelKey);
        const ts = useTreeViewStore.getState();
        const td = ts.treeData;
        if (td) {
            if (td.model_key === modelKey) {
                // Single un-containered root (first/only model).
                ts.clearTreeData();
            } else if (Array.isArray(td.children)) {
                const remaining = td.children.filter((c) => c.model_key !== modelKey);
                if (remaining.length !== td.children.length) {
                    if (remaining.length === 0) ts.clearTreeData();
                    else ts.setTreeData({...td, children: remaining});
                }
            }
        }
    }

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

    // Mirror what clear_loaded_model does per-group: detach
    // children + remove from the parent scene so threejs can
    // free the GPU buffers on the next frame.
    group.clear();
    sceneRef.current?.remove(group);
    // On-demand render loop won't tick until the next OrbitControls
    // 'change' event — without this kick the just-removed group
    // keeps rendering on the canvas until the user rotates.
    requestRender();
}
