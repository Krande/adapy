import {getMeshFromName} from "../scene/getMeshFromName";
import {getDrawRangeByName} from "../mesh_select/getDrawRangeByName";
import {NodeApi} from "react-arborist";
import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";
import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";


function get_nodes_recursive(node: NodeApi, nodes: NodeApi[]) {
    nodes.push(node);
    if (node.children != null && node.children.length > 0) {
        for (let child of node.children) {
            get_nodes_recursive(child, nodes);
        }
    }
}

function get_mesh_and_draw_ranges(nodes: NodeApi[]) {
    let meshes_and_ranges: [CustomBatchedMesh, string][] = [];
    for (let node of nodes) {
        let node_name = node.data.name;
        let key = node.data.key;
        let rangeId = node.data.rangeId;
        let mesh = node.data.meshRef;
        //console.time("getDrawRangeByName");
        if (!key && !rangeId) {
            let draw_range_data = getDrawRangeByName(node_name);
            //console.timeEnd("getDrawRangeByName");
            if (!draw_range_data) {
                continue;
            }
            const [key, rangeId, start, count] = draw_range_data;
            let mesh_node_name = key.split("_")[2];
            //console.time("getMeshFromName");
            if (!mesh) {
                let mesh = getMeshFromName(mesh_node_name);
            }
        }

        //console.timeEnd("getMeshFromName");
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
        console.time("get_mesh_and_draw_ranges");
        let const_meshes_and_draw_ranges = get_mesh_and_draw_ranges(nodes);
        console.timeEnd("get_mesh_and_draw_ranges");

        console.time("addBatchofMeshes");
        useSelectedObjectStore.getState().clearSelectedObjects();
        useSelectedObjectStore.getState().addBatchofMeshes(const_meshes_and_draw_ranges);
        console.timeEnd("addBatchofMeshes");
    } else {
        useSelectedObjectStore.getState().clearSelectedObjects();
    }
}

