// "Flip the loaded geometry": temporarily swap the displayed model for another
// build's GLB so the user can inspect the *other* model in full (section planes,
// selection, tree) instead of only seeing it as a diff overlay. Used by the diff
// utility panel — it flips to whatever ``compare_ref`` points at.
//
// The compared GLB is loaded through the normal model pipeline
// (overlay_file_in_scene -> setupModelLoaderAsync), so it becomes a real
// CustomBatchedMesh with its own picker + section clipping, not a flat overlay.
// The originals are hidden (not removed) so flipping back is instant and cheap.
import * as THREE from "three";

import {sceneRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useModelState, loadedSourceGroups} from "@/state/modelState";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {useTreeViewStore} from "@/state/treeViewStore";
import {TreeNodeData} from "@/components/tree_view/CustomNode";

interface FlipState {
    flippedKey: string;
    hidden: THREE.Object3D[];            // original groups we hid
    prevSourceName: string | null;       // loaded-source bookkeeping to restore
    prevSourceNames: ReadonlySet<string>;
    prevTreeData: TreeNodeData | null;   // tree to restore on unflip
    prevScopeId: string | null;
    prevScopeName: string | null;
}

let _flip: FlipState | null = null;

export function isFlipped(): boolean {
    return _flip !== null;
}

export function flippedKey(): string | null {
    return _flip?.flippedKey ?? null;
}

/** Hide the current model(s) and load ``compareKey`` (a versions/*.glb storage
 *  key) as the displayed model. No-op if already flipped. */
export async function flipToCompared(compareKey: string): Promise<void> {
    if (_flip) return;
    if (!compareKey || !sceneRef.current) return;

    const ms = useModelState.getState();
    const prevSourceName = ms.loadedSourceName;
    const prevSourceNames = ms.loadedSourceNames;
    const ts = useTreeViewStore.getState();
    const prevTreeData = ts.treeData;
    const prevScopeId = ts.scopeNodeId;
    const prevScopeName = ts.scopeNodeName;

    const hidden: THREE.Object3D[] = [];
    for (const g of loadedSourceGroups.values()) {
        if (g.visible) {
            g.visible = false;
            hidden.push(g);
        }
    }
    // Set the flip marker BEFORE the await so a double-click can't start a
    // second load while the first is in flight.
    _flip = {flippedKey: compareKey, hidden, prevSourceName, prevSourceNames,
             prevTreeData, prevScopeId, prevScopeName};

    try {
        // translate=true inside overlay_file_in_scene reuses the cached
        // centering translation, so the compared model lands in the same frame.
        await overlay_file_in_scene(compareKey);
    } catch (err) {
        // Roll back the hide so the user isn't left with an empty scene.
        for (const g of hidden) g.visible = true;
        _flip = null;
        requestRender();
        throw err;
    }
    // The compared model is now an extra tree root (cacheAndBuildTree) — that's
    // the desired "tree follows the flip". prevTreeData is kept only so unflip
    // can drop that root again.
    requestRender();
}

/** Remove the compared model and restore the original(s). No-op if not flipped. */
export function unflip(): void {
    if (!_flip) return;
    const {flippedKey: key, hidden, prevSourceName, prevSourceNames,
           prevTreeData, prevScopeId, prevScopeName} = _flip;
    _flip = null;

    const group = useModelState.getState().unregisterLoadedSource(key);
    if (group) {
        group.parent?.remove(group);
        group.traverse((o) => {
            const m = o as THREE.Mesh;
            if (m.geometry) m.geometry.dispose?.();
            const mat = m.material as THREE.Material | THREE.Material[] | undefined;
            if (Array.isArray(mat)) mat.forEach((x) => x.dispose?.());
            else mat?.dispose?.();
        });
    }
    for (const g of hidden) g.visible = true;
    // Restore the loaded-source bookkeeping the flip load mutated.
    useModelState.setState({loadedSourceName: prevSourceName, loadedSourceNames: prevSourceNames});
    // Drop the compared model's tree root (restore the pre-flip tree + scope).
    const ts = useTreeViewStore.getState();
    if (prevTreeData) ts.setTreeData(prevTreeData);
    else ts.clearTreeData();
    ts.setScope(prevScopeId, prevScopeName);
    requestRender();
}
