import {getMeshFromName} from "../scene/getMeshFromName";
import {getDrawRangeByName} from "../mesh_select/getDrawRangeByName";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {NodeApi} from "react-arborist";
import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";
import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";


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

function get_nodes_recursive(node: NodeApi, nodes: NodeApi[]) {
    nodes.push(node);
    if (node.children != null && node.children.length > 0) {
        for (let child of node.children) {
            get_nodes_recursive(child, nodes);
        }
    }
}

function get_mesh_and_draw_ranges(nodes: NodeApi[]) {
    let meshes_and_ranges = [];
    for (let node of nodes) {
        let node_name = node.data.name;
        let draw_range_data = getDrawRangeByName(node_name);
        if (!draw_range_data) {
            continue;
        }
        const [key, rangeId, start, count] = draw_range_data;
        let mesh_node_name = key.split("_")[2];

        let mesh = getMeshFromName(mesh_node_name);
        if (!mesh) {
            continue;
        }
        meshes_and_ranges.push([mesh, rangeId]);
    }
    return meshes_and_ranges;
}

export function handleTreeSelectionChange(ids: NodeApi[]) {
    if (ids.length > 0) {
        let nodes: NodeApi[] = [];
        for (let node of ids) {
            get_nodes_recursive(node, nodes);
        }
        let const_meshes_and_draw_ranges = get_mesh_and_draw_ranges(nodes);
        useSelectedObjectStore.getState().clearSelectedObjects();
        useSelectedObjectStore.getState().addBatchofMeshes(const_meshes_and_draw_ranges);
    } else {
        useSelectedObjectStore.getState().clearSelectedObjects();
    }
}

