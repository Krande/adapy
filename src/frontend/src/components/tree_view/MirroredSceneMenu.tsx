// The scene tree's row menu in a pop-out window: what the main window's menu
// (`./SceneTreeMenu`) offers that a pop-out can do -- visibility and framing,
// sent to the main window (`@/layout/sceneActions`); copying the rows' names;
// expanding or collapsing below the row, which is this window's own. The rest
// (groups, related elements, other providers, unload, the selection box) reads
// the scene and stays in the main window's menu.

import React from "react";

import {KebabMenuItem, PositionedMenu} from "@/components/common/PositionedMenu";
import {useSceneActions} from "@/layout/sceneActions";
import {useSceneMirrorStore} from "@/state/sceneMirrorStore";
import {useSceneMenuStore, type SceneMenuState} from "@/state/sceneMenuStore";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import {setSubtreeOpen} from "@/utils/tree_view/sceneTreeActions";

import type {TreeNodeData} from "./CustomNode";
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
    if (!menu) return null;
    // The FEA set and property rows have a menu of their own, the same in both windows.
    if (isFeaMenu(menu.rows.length ? menu.rows : [menu.row])) return <FeaSceneMenu {...menu} onClose={close}/>;
    return <MirroredSceneMenu {...menu} onClose={close}/>;
};

const MirroredSceneMenu: React.FC<SceneMenuState & {onClose: () => void}> = ({row, rows: selection, node, x, y, onClose}) => {
    const actions = useSceneActions();
    const rows = selection.length ? selection : [row];
    useSceneMirrorStore((s) => s.visVersion);
    const {drawn, allHidden, anyHidden} = mirroredRowsVisibility(rows, useSceneMirrorStore.getState().visibility);
    const what = rows.length === 1 ? "" : ` ${rows.length} rows`;
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
            key: "copy",
            label: rows.length > 1 ? `Copy${what} names` : "Copy name",
            separatorBefore: true,
            onClick: () => void writeToClipboard(rows.map((r) => r.name).join("\n")),
        },
    ];
    if (node?.isInternal) {
        items.push(
            {key: "expand", label: "Expand all below", onClick: () => setSubtreeOpen(node, true), separatorBefore: true},
            {key: "collapse", label: "Collapse all below", onClick: () => setSubtreeOpen(node, false)},
        );
    }
    return <PositionedMenu items={items} anchor={{kind: "point", x, y}} onClose={onClose} header={row.name}/>;
};
