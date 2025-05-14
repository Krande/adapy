import {NodeApi} from "react-arborist";
import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";
import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";
import {modelStore} from "../../state/model_worker/modelStore";
import {modelKeyMapRef} from "../../state/refs";


function get_nodes_recursive(node: NodeApi, nodes: NodeApi[]) {
    nodes.push(node);
    if (node.children != null && node.children.length > 0) {
        for (let child of node.children) {
            get_nodes_recursive(child, nodes);
        }
    }
}

async function get_mesh_and_draw_ranges(nodes: NodeApi[]) {
    let meshes_and_ranges: [CustomBatchedMesh, string][] = [];
    for (let node of nodes) {
        let rangeId = node.data.id;
        let node_name = node.data.node_name;
        let scene = modelKeyMapRef.current?.get(node.data.key)
        if (!scene) {
            console.warn("No scene found for node:", node);
            continue;
        }
        let mesh = scene.getObjectByName(node_name) as CustomBatchedMesh;
        if (!mesh) {
            continue;
        }
        meshes_and_ranges.push([mesh, rangeId]);
    }
    return meshes_and_ranges;
}

export async function handleTreeSelectionChange(ids: NodeApi[]) {
    if (ids.length > 0) {
        let nodes: NodeApi[] = [];
        for (let node of ids) {
            get_nodes_recursive(node, nodes);
        }
        console.time("get_mesh_and_draw_ranges");
        let const_meshes_and_draw_ranges = await get_mesh_and_draw_ranges(nodes);
        console.timeEnd("get_mesh_and_draw_ranges");

        console.time("addBatchofMeshes");
        useSelectedObjectStore.getState().clearSelectedObjects();
        useSelectedObjectStore.getState().addBatchofMeshes(const_meshes_and_draw_ranges);
        console.timeEnd("addBatchofMeshes");
    } else {
        useSelectedObjectStore.getState().clearSelectedObjects();
    }
}

