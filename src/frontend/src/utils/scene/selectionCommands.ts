/**
 * Select all / invert selection over the loaded models (Edit menu, Ctrl+A). Each is one undo
 * entry, as a box or tree selection is; hidden ranges are never taken (`selectionSets`).
 */

import * as THREE from "three";

import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {allShownRanges, invertedRanges, selectionLabel} from "@/utils/mesh_select/selectionSets";
import {recordViewerAction} from "@/utils/scene/viewerHistory";

/** The scene's batched meshes, selection highlight overlays left out. */
function batchedMeshes(): CustomBatchedMesh[] {
    const scene = getViewerRuntime().scene.current;
    const out: CustomBatchedMesh[] = [];
    scene?.traverse((o) => {
        if (o instanceof CustomBatchedMesh) out.push(o);
    });
    return out;
}

/** The selection as ids per batched mesh: entries keyed by a wrapper are folded onto the batched
 *  meshes under it, as `hideUnselectedRanges` does. */
function selectedPerMesh(): Map<CustomBatchedMesh, Set<string>> {
    const out = new Map<CustomBatchedMesh, Set<string>>();
    const add = (mesh: CustomBatchedMesh, ids: Iterable<string>) => {
        const set = out.get(mesh) ?? new Set<string>();
        for (const id of ids) set.add(id);
        out.set(mesh, set);
    };
    useSelectedObjectStore.getState().selectedObjects.forEach((ids, obj) => {
        if (obj instanceof CustomBatchedMesh) add(obj, ids);
        else (obj as THREE.Object3D).traverse((c) => {
            if (c instanceof CustomBatchedMesh) add(c, ids);
        });
    });
    return out;
}

function replaceSelection(batch: [CustomBatchedMesh, string][], label: string): number {
    recordViewerAction("select", () => {
        const sel = useSelectedObjectStore.getState();
        sel.clearSelectedObjects();
        if (batch.length) sel.addBatchofMeshes(batch);
        const info = useObjectInfoStore.getState();
        info.setName(batch.length ? label : null);
        info.setSelectedNodeId(null);
    });
    requestRender();
    return batch.length;
}

/** Select every shown element of every loaded model. Returns how many. */
export function selectAllRanges(): number {
    const batch = allShownRanges(batchedMeshes());
    return replaceSelection(batch, selectionLabel("all", batch.length));
}

/** Select the shown elements that are not selected, and deselect the rest. Returns how many are selected after. */
export function invertSelection(): number {
    const batch = invertedRanges(batchedMeshes(), selectedPerMesh());
    return replaceSelection(batch, selectionLabel("inverted", batch.length));
}
