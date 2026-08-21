import React from "react";
import {useViewerStores} from "@/state/AdaViewerContext";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import type {SelectionSnapshot} from "./propertyProviders";

/**
 * Derive the current selection summary from the stores.
 *
 * READ-ONLY. This hook exists so `match` predicates have something cheap to test
 * against; it deliberately owns no state and mutates nothing. The precedence and the
 * counting rules are copied from ObjectInfoBoxComponent so the Properties panel and the
 * classic info box agree about what is selected:
 *
 *   * a cellbuilder selection wins over a mesh selection — a click that lands on a
 *     builder cell reads as that cell, not as result geometry behind it;
 *   * "count" is total draw ranges, not meshes: one per clicked element, which is what
 *     the user thinks they selected regardless of how many meshes back it.
 */
export function useSelectionSnapshot(): SelectionSnapshot {
    const {useObjectInfoStore, useSelectedObjectStore, useTreeViewStore} = useViewerStores();

    const name = useObjectInfoStore((s) => s.name);
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const treeData = useTreeViewStore((s) => s.treeData);

    const cbActive = useCellBuilderStore((s) => s.active !== null);
    const cbSelection = useCellBuilderStore((s) => s.selection);
    const cbSelectedIds = useCellBuilderStore((s) => s.selectedCellIds);
    const cbCells = useCellBuilderStore((s) => s.cells);

    return React.useMemo(() => {
        const cellCtx = cbActive && cbSelection !== null;

        let rangeCount = 0;
        selectedObjects.forEach((ids) => {
            rangeCount += ids.size;
        });

        const hasEntities = treeData != null || (cbActive && Object.keys(cbCells).length > 0);

        if (cellCtx) {
            return {
                kind: "cell",
                name: (cbSelection && cbCells[cbSelection.cellId]?.name) || null,
                count: cbSelectedIds.length,
                hasEntities,
                cellBuilderActive: cbActive,
            };
        }
        if (rangeCount > 0 || name) {
            return {kind: "mesh", name: name || null, count: rangeCount, hasEntities, cellBuilderActive: cbActive};
        }
        return {kind: "none", name: null, count: 0, hasEntities, cellBuilderActive: cbActive};
    }, [name, selectedObjects, treeData, cbActive, cbSelection, cbSelectedIds, cbCells]);
}
