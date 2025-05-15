import {NodeApi} from "react-arborist";
import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";
import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";
import {modelKeyMapRef} from "../../state/refs";
import {useObjectInfoStore} from "../../state/objectInfoStore";


export async function get_nodes_recursive(node: NodeApi, nodes: NodeApi[]) {
    nodes.push(node);
    if (node.children != null && node.children.length > 0) {
        for (let child of node.children) {
            await get_nodes_recursive(child, nodes);
        }
    }
}

async function get_mesh_and_draw_ranges(nodes: NodeApi[]) {
    let meshes_and_ranges: [CustomBatchedMesh, string][] = [];
    for (let node of nodes) {
        let rangeId = node.data.rangeId;
        let node_name = node.data.node_name;
        let scene = modelKeyMapRef.current?.get(node.data.model_key)
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
    const selectedObjectStore = useSelectedObjectStore.getState();

    if (ids.length > 0) {
        let nodes: NodeApi[] = [];
        for (let node of ids) {
            await get_nodes_recursive(node, nodes);
        }
        let const_meshes_and_draw_ranges = await get_mesh_and_draw_ranges(nodes);

        selectedObjectStore.clearSelectedObjects();
        selectedObjectStore.addBatchofMeshes(const_meshes_and_draw_ranges);
        const last_node = nodes[nodes.length - 1];
        const last_selected = last_node.data.name;
        useObjectInfoStore.getState().setName(last_selected);
    } else {
        selectedObjectStore.clearSelectedObjects();
    }
}

