// The scene tree's row menu in a pop-out window: what the main window's menu
// (`./SceneTreeMenu`) offers that a pop-out can do -- visibility and framing,
// sent to the main window (`@/layout/sceneActions`); copying the rows' names;
// expanding or collapsing below the row, which is this window's own; the selection box
// and unloading the models (sent to the main window); saved groups (a prompt of this
// window's own, over the same groups service); and the related elements (listed here
// from the relations index, run in the main window). What stays in the main window's
// menu is loading a node from another provider, which reads the Sources tab.

import React from "react";

import {KebabMenuItem, PositionedMenu} from "@/components/common/PositionedMenu";
import {useSceneActions} from "@/layout/sceneActions";
import {useRelatedActions} from "@/hooks/useRelatedActions";
import {useTreeViewStore} from "@/state/treeViewStore";
import {modelRootOf} from "@/utils/export/selectionExport";
import {buildTreeIndices} from "@/utils/tree_view/treeGraph";
import {useSceneMirrorStore} from "@/state/sceneMirrorStore";
import {useSceneMenuStore, type SceneMenuState} from "@/state/sceneMenuStore";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import {setSubtreeOpen} from "@/utils/tree_view/sceneTreeActions";

import type {TreeNodeData} from "./CustomNode";
import {AddToGroupPromptHost, addToGroupBlocker, useAddToGroupPromptStore} from "./AddToGroupPrompt";
import {FeaSceneMenu} from "./FeaSceneMenu";
import {isFeaMenu} from "./feaRowMenu";

/** What the mirrored visibility says of `rows`: any of them drawn at all, all of them hidden, any of them hidden. */
export function mirroredRowsVisibility(rows: readonly TreeNodeData[], visibility: ReadonlyMap<string, number>): {drawn: boolean; allHidden: boolean; anyHidden: boolean} {
    let drawn = false;
    let allHidden = true;
    let anyHidden = false;
    for (const r of rows) {
        const code = visibility.get(r.id) ?? 0;
        if (code === 3) continue;
        drawn = true;
        if (code !== 1) allHidden = false;
        if (code === 1 || code === 2) anyHidden = true;
    }
    return {drawn, allHidden: drawn && allHidden, anyHidden};
}

/** The menu, drawn by the pop-out's tree when a row asks for it (`useSceneMenuStore`). */
export const MirroredSceneMenuHost: React.FC = () => {
    const menu = useSceneMenuStore((s) => s.menu);
    const close = useSceneMenuStore((s) => s.close);
    // The group prompt outlives the menu (it closes on any click): host it with no menu open too.
    if (!menu) return <AddToGroupPromptHost/>;
    // The FEA set and property rows have a menu of their own, the same in both windows.
    return (
        <>
            {isFeaMenu(menu.rows.length ? menu.rows : [menu.row]) ? <FeaSceneMenu {...menu} onClose={close}/> : <MirroredSceneMenu {...menu} onClose={close}/>}
            <AddToGroupPromptHost/>
        </>
    );
};

const MirroredSceneMenu: React.FC<SceneMenuState & {onClose: () => void}> = ({row, rows: selection, node, x, y, onClose}) => {
    const actions = useSceneActions();
    const rows = selection.length ? selection : [row];
    useSceneMirrorStore((s) => s.visVersion);
    const {drawn, allHidden, anyHidden} = mirroredRowsVisibility(rows, useSceneMirrorStore.getState().visibility);
    const what = rows.length === 1 ? "" : ` ${rows.length} rows`;
    const treeData = useTreeViewStore((s) => s.treeData);
    const groupBlocker = addToGroupBlocker(rows);
    const related = useRelatedActions(rows.length === 1 ? row : null);
    const noGeometry = drawn ? undefined : "Nothing under the selection is drawn in the scene";
    const items: KebabMenuItem[] = [
        {key: "goto", label: "Go to  (Shift+F)", onClick: () => actions.fitRows(rows), disabled: !drawn, title: noGeometry},
        {key: "hide", label: `Hide${what}  (Shift+H)`, onClick: () => actions.hideRows(rows), disabled: !drawn || allHidden, title: noGeometry, separatorBefore: true},
        {
            key: "show",
            label: `Unhide${what}`,
            onClick: () => actions.showRows(rows),
            disabled: !anyHidden,
            title: anyHidden ? undefined : "Nothing under the selection is hidden",
        },
        {key: "isolate", label: "Hide all others  (Shift+I)", onClick: () => actions.isolateRows(rows), disabled: !drawn, title: noGeometry},
        {key: "show-all", label: "Unhide all  (Shift+U)", onClick: actions.showAll},
        {
            key: "bbox-select",
            label: "Select with bounding box",
            onClick: () => actions.boxAroundSelection(),
            disabled: !drawn,
            title: noGeometry ?? "A box around the selection that selects what it touches or contains",
            separatorBefore: true,
        },
        {
            key: "copy",
            label: rows.length > 1 ? `Copy${what} names` : "Copy name",
            separatorBefore: true,
            onClick: () => void writeToClipboard(rows.map((r) => r.name).join("\n")),
        },
        {
            key: "add-to-group",
            label: rows.length > 1 ? `Add${what} to group…` : "Add to group…",
            onClick: () => useAddToGroupPromptStore.getState().open({rows, x, y}),
            disabled: groupBlocker !== null,
            title: groupBlocker ?? "Put the selection in a saved group -- an existing one, or a new one",
        },
    ];
    // What the element is related to: listed from the relations index, run in the main window.
    if (related.loading) {
        items.push({key: "related-wait", label: "Related…", onClick: () => undefined, disabled: true, title: "Reading what this element is related to", separatorBefore: true});
    } else {
        related.actions.forEach((a, i) =>
            items.push({key: a.key, label: a.label, title: a.title, disabled: a.disabled, onClick: () => actions.runRelated(row, a.key), separatorBefore: i === 0}),
        );
    }
    // Unload acts on whole models: the loaded model each selected row belongs to, each once.
    const models = new Map<string, string>();
    if (treeData) {
        const idx = buildTreeIndices(treeData);
        for (const r of rows) {
            const top = modelRootOf(r, idx);
            if (top?.source_name) models.set(top.source_name, top.name);
        }
    }
    items.push({
        key: "unload",
        label: models.size > 1 ? `Unload ${models.size} models` : "Unload model",
        onClick: () => actions.unloadModels([...models.keys()]),
        disabled: models.size === 0,
        title: models.size ? [...models.values()].join("\n") : "Not part of a loaded model that can be unloaded",
        destructive: true,
        separatorBefore: true,
    });
    if (node?.isInternal) {
        items.push(
            {key: "expand", label: "Expand all below", onClick: () => setSubtreeOpen(node, true), separatorBefore: true},
            {key: "collapse", label: "Collapse all below", onClick: () => setSubtreeOpen(node, false)},
        );
    }
    return <PositionedMenu items={items} anchor={{kind: "point", x, y}} onClose={onClose} header={row.name}/>;
};
