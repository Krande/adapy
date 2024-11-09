import {getMeshFromName} from "../scene/getMeshFromName";
import {getDrawRangeByName} from "../mesh_select/getDrawRangeByName";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {NodeApi} from "react-arborist";
import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";


function highlightNode(node: NodeApi) {
    let node_name = node.data.name;

    if (node.children != null && node.children.length > 0) {
        for (let child of node.children) {
            highlightNode(child);
        }
        return;
    }

    let draw_range_data = getDrawRangeByName(node_name);
    if (!draw_range_data) {
        console.error("Could not find draw range data for", node_name);
        return;
    }
    const [key, rangeId, start, count] = draw_range_data;
    let mesh_node_name = key.split("_")[2];

    let mesh = getMeshFromName(mesh_node_name);
    if (!mesh) {
        console.error("Could not find mesh for", mesh_node_name);
        return;
    }
    useObjectInfoStore.getState().setName(node_name);
    useSelectedObjectStore.getState().addSelectedObject(mesh, rangeId);
}

export function handleTreeSelectionChange(ids: NodeApi[]) {
    if (ids.length > 0) {
        useSelectedObjectStore.getState().clearSelectedObjects();
        for (let node of ids) {
            highlightNode(node);
        }
    } else {
        useSelectedObjectStore.getState().clearSelectedObjects();
    }
}