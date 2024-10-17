import React from "react";
import {useTreeViewStore} from "../../state/treeViewStore";

export function handleClickedNode(event: React.SyntheticEvent, itemIds: string | null) {
        if (itemIds !== null) {
            let node_name = (event as React.BaseSyntheticEvent).currentTarget.innerText;
            console.log("itemIds", itemIds);
            console.log("node_name", node_name);
            useTreeViewStore.getState().setSelectedNodeId(itemIds);
        }
}