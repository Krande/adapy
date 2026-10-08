// The Scene tree's right-click menu: visibility, framing and copying for the selected rows, and
// expanding or collapsing the row it was opened on.

import React, {useMemo} from "react";
import type {NodeApi} from "react-arborist";

import {KebabMenuItem, PositionedMenu} from "@/components/common/PositionedMenu";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {copySelectionNames} from "@/utils/clipboard/copySelectionNames";
import {
    frameSelection,
    hiddenCount,
    hideRows,
    isolateRows,
    setSubtreeOpen,
    showAll,
    showRows,
} from "@/utils/tree_view/sceneTreeActions";

import type {TreeNodeData} from "./CustomNode";

export interface SceneTreeMenuState {
    /** The row right-clicked. */
    node: NodeApi<TreeNodeData>;
    x: number;
    y: number;
}

const SceneTreeMenu: React.FC<SceneTreeMenuState & {onClose: () => void}> = ({node, x, y, onClose}) => {
    // The right-click made the row part of the selection, so the selection is what the menu acts on.
    const selected = node.tree.selectedNodes.length ? node.tree.selectedNodes : [node];
    const rows = selected.map((n) => n.data);
    const {hidden, total} = useMemo(() => hiddenCount(rows), [selected]); // eslint-disable-line react-hooks/exhaustive-deps
    const what = selected.length === 1 ? "" : ` ${selected.length} rows`;
    const noGeometry = total === 0 ? "Nothing under the selection is drawn in the scene" : undefined;

    const items: KebabMenuItem[] = [
        {
            key: "goto",
            label: "Go to  (Shift+F)",
            onClick: frameSelection,
            disabled: total === 0,
            title: noGeometry,
        },
        {
            key: "hide",
            label: `Hide${what}  (Shift+H)`,
            onClick: () => hideRows(rows),
            disabled: total === 0 || hidden === total,
            title: noGeometry,
            separatorBefore: true,
        },
        {
            key: "show",
            label: hidden && hidden < total ? `Unhide${what}  (${hidden} hidden)` : `Unhide${what}`,
            onClick: () => showRows(rows),
            disabled: hidden === 0,
            title: hidden === 0 ? "Nothing under the selection is hidden" : undefined,
        },
        {
            key: "isolate",
            label: "Hide all others  (Shift+I)",
            onClick: () => isolateRows(rows),
            disabled: total === 0,
            title: noGeometry,
        },
        {key: "show-all", label: "Unhide all  (Shift+U)", onClick: showAll},
        {
            key: "copy",
            label: "Copy names  (Ctrl+C)",
            separatorBefore: true,
            onClick: () => void copySelectionNames(useSelectedObjectStore.getState().selectedObjects),
        },
    ];
    if (node.isInternal) {
        items.push(
            {key: "expand", label: "Expand all below", onClick: () => setSubtreeOpen(node, true), separatorBefore: true},
            {key: "collapse", label: "Collapse all below", onClick: () => setSubtreeOpen(node, false)},
        );
    }

    return <PositionedMenu items={items} anchor={{kind: "point", x, y}} onClose={onClose} header={node.data.name}/>;
};

export default SceneTreeMenu;
