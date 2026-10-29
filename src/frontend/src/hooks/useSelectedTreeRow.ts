import {useMemo} from "react";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useViewerStores} from "@/state/AdaViewerContext";
import {buildTreeIndices, type TreeIndices} from "@/utils/tree_view/treeGraph";

/** The one selected element's scene-tree row -- the element picked in 3D (by model key and range
 *  id), else the row picked in the tree -- with the tree's indices; null row when there is no single
 *  selected element. */
export function useSelectedTreeRow(): {row: TreeNodeData | null; indices: TreeIndices | null} {
    const {useObjectInfoStore, useTreeViewStore, useSelectedObjectStore} = useViewerStores();
    const selectedNodeId = useObjectInfoStore((s) => s.selectedNodeId);
    const name = useObjectInfoStore((s) => s.name);
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const treeData = useTreeViewStore((s) => s.treeData);
    const findNodeByRangeId = useTreeViewStore((s) => s.findNodeByRangeId);

    const indices = useMemo(() => (treeData ? buildTreeIndices(treeData) : null), [treeData]);
    const row = useMemo(() => {
        if (!indices) return null;
        let picked: TreeNodeData | null = null;
        let count = 0;
        for (const [mesh, ranges] of selectedObjects) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const key: string | undefined = (mesh as any).unique_key ?? mesh.userData?.unique_hash;
            for (const rid of ranges) {
                count++;
                if (key) picked = findNodeByRangeId(key, rid);
            }
        }
        if (count === 1 && picked) return indices.byId.get(picked.id) ?? picked;
        return selectedNodeId ? indices.byId.get(selectedNodeId) ?? null : null;
    }, [indices, selectedObjects, selectedNodeId, findNodeByRangeId, name]); // eslint-disable-line react-hooks/exhaustive-deps
    return {row, indices};
}
