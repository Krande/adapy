import {useMemo} from "react";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useViewerStores} from "@/state/AdaViewerContext";
import {useSelectionRefs} from "@/viewport/api/useViewportSelection";
import {buildTreeIndices, type TreeIndices} from "@/utils/tree_view/treeGraph";

/** The one selected element's scene-tree row -- the element picked in 3D (by model key and range
 *  id), else the row picked in the tree -- with the tree's indices; null row when there is no single
 *  selected element. */
export function useSelectedTreeRow(): {row: TreeNodeData | null; indices: TreeIndices | null} {
    const {useObjectInfoStore, useTreeViewStore} = useViewerStores();
    const selectedNodeId = useObjectInfoStore((s) => s.selectedNodeId);
    const name = useObjectInfoStore((s) => s.name);
    // What is selected on the viewport on screen, whichever it is.
    const selected = useSelectionRefs();
    const treeData = useTreeViewStore((s) => s.treeData);
    const findNodeByRangeId = useTreeViewStore((s) => s.findNodeByRangeId);

    const indices = useMemo(() => (treeData ? buildTreeIndices(treeData) : null), [treeData]);
    const row = useMemo(() => {
        if (!indices) return null;
        let picked: TreeNodeData | null = null;
        let count = 0;
        for (const ref of selected) {
            count++;
            picked = findNodeByRangeId(ref.model, ref.id);
        }
        if (count === 1 && picked) return indices.byId.get(picked.id) ?? picked;
        return selectedNodeId ? indices.byId.get(selectedNodeId) ?? null : null;
    }, [indices, selected, selectedNodeId, findNodeByRangeId, name]); // eslint-disable-line react-hooks/exhaustive-deps
    return {row, indices};
}
