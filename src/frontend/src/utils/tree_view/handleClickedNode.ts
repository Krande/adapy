import {NodeApi} from "react-arborist";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";
import {modelKeyMapRef} from "@/state/refs";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useTreeViewStore} from "@/state/treeViewStore";


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
        // node_name (the merged-mesh name from the worker's elementToMesh map) is
        // authoritative; null means this row is a pure group node with no draw
        // range. The old display-name fallback could only ever bind a WRONG
        // same-named mesh — display names repeat thousands of times in real models.
        const mesh = node_name ? (scene.getObjectByName(node_name) as CustomBatchedMesh) : undefined;
        if (!mesh) {
            continue;
        }
        meshes_and_ranges.push([mesh, rangeId]);
    }
    return meshes_and_ranges;
}

export async function handleTreeSelectionChange(ids: NodeApi[]) {
    const selectedObjectStore = useSelectedObjectStore.getState();

    // Scope tree search to the selected node's subtree (cleared when nothing
    // is selected, so search spans all roots again).
    useTreeViewStore.getState().setScope(
        ids.length > 0 ? ids[0].id : null,
        ids.length > 0 ? (ids[0].data?.name ?? null) : null,
    );

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

