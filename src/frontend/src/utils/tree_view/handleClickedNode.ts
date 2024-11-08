import React from "react";
import {useTreeViewStore} from "../../state/treeViewStore";
import {getMeshFromName} from "../scene/getMeshFromName";
import * as THREE from 'three';
import {getDrawRangeByName} from "../mesh_select/getDrawRangeByName";
import {deselectObject} from "../mesh_select/deselectObject";
import {perform_selection} from "../mesh_select/handleClickMesh";

export function handleClickedNode(event: React.MouseEvent, itemIds: string | null) {
        if (itemIds !== null) {
            let node_name = (event as React.BaseSyntheticEvent).currentTarget.innerText;
            console.log("itemIds", itemIds);
            console.log("node_name", node_name);

            let draw_range_data = getDrawRangeByName(node_name);
            if (!draw_range_data) {
                console.error("Could not find draw range data for", node_name);
                return;
            }
            const [key, rangeId, start, count] = draw_range_data;
            let mesh_node_name = key.split("_")[2];

            let mesh = getMeshFromName(mesh_node_name);

            // if mesh is not null and mesh is instance of THREE.Mesh
            if (mesh && !(mesh instanceof THREE.LineSegments) && !(mesh instanceof THREE.Points)) {
                // console.log("mesh", mesh);
                let shiftKey = event.shiftKey;
                perform_selection(mesh, shiftKey, rangeId);
            } else {
                deselectObject();
            }
            useTreeViewStore.getState().setSelectedNodeId(itemIds);
        }
}