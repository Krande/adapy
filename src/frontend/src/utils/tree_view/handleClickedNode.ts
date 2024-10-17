import React from "react";
import {useTreeViewStore} from "../../state/treeViewStore";
import {getMeshFromName} from "../scene/getMeshFromName";
import * as THREE from 'three';
import {setSelectedMesh} from "../mesh_select/setSelectedMesh";

export function handleClickedNode(event: React.SyntheticEvent, itemIds: string | null) {
        if (itemIds !== null) {
            let node_name = (event as React.BaseSyntheticEvent).currentTarget.innerText;
            console.log("itemIds", itemIds);
            console.log("node_name", node_name);
            let mesh = getMeshFromName(node_name);
            if (mesh) {
                console.log("mesh", mesh);
                setSelectedMesh(mesh as THREE.Mesh, 0);
            }
            useTreeViewStore.getState().setSelectedNodeId(itemIds);
        }
}