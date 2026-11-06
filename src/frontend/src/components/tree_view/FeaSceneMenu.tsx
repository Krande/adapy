// The Scene tree's row menu on FEA set and property rows (`./feaRowMenu`), in
// the main window and in a pop-out: the items are scene actions.

import React, {useSyncExternalStore} from "react";

import {PositionedMenu} from "@/components/common/PositionedMenu";
import {useSceneActions} from "@/layout/sceneActions";
import type {SceneMenuState} from "@/state/sceneMenuStore";
import {feaTreeView, subscribeFeaTreeView} from "@/utils/scene/fea/feaTree";
import {setSubtreeOpen} from "@/utils/tree_view/sceneTreeActions";

import {feaRowMenuItems} from "./feaRowMenu";

export const FeaSceneMenu: React.FC<SceneMenuState & {onClose: () => void}> = ({row, rows: selection, node, x, y, onClose}) => {
    const actions = useSceneActions();
    const view = useSyncExternalStore(subscribeFeaTreeView, feaTreeView, feaTreeView);
    const rows = selection.length ? selection : [row];
    const items = feaRowMenuItems(rows, view, actions);
    if (node?.isInternal) {
        items.push(
            {key: "expand", label: "Expand all below", onClick: () => setSubtreeOpen(node, true), separatorBefore: true},
            {key: "collapse", label: "Collapse all below", onClick: () => setSubtreeOpen(node, false)},
        );
    }
    return <PositionedMenu items={items} anchor={{kind: "point", x, y}} onClose={onClose} header={row.name}/>;
};

export default FeaSceneMenu;
