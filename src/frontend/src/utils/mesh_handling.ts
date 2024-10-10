import {useSelectedObjectStore} from "../state/selectedObjectStore";
import {TreeNode, useTreeViewStore} from "../state/treeViewStore";
import * as THREE from "three";
import {ThreeEvent} from "@react-three/fiber";
import {useObjectInfoStore} from "../state/objectInfoStore";
import {query_ws_server_mesh_info} from "./mesh_select/send_mesh_selected_info_callback";


const selectedMaterial = new THREE.MeshStandardMaterial({color: 'blue', side: THREE.DoubleSide});
const defaultMaterial = new THREE.MeshStandardMaterial({color: 'white', side: THREE.DoubleSide});


function deselectObject() {
    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalMaterial = useSelectedObjectStore.getState().originalMaterial;
    if (selectedObject) {
        selectedObject.material = originalMaterial ? originalMaterial : defaultMaterial;
        useSelectedObjectStore.getState().setOriginalMaterial(null);
        useSelectedObjectStore.getState().setSelectedObject(null);
    }
}


export function handleMeshSelected(event: ThreeEvent<PointerEvent>) {
    event.stopPropagation();

    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalMaterial = useSelectedObjectStore.getState().originalMaterial;
    const mesh = event.object as THREE.Mesh;

    if (selectedObject !== mesh) {
        if (selectedObject) {
            selectedObject.material = originalMaterial ? originalMaterial : defaultMaterial;
        }
        const material = mesh.material as THREE.MeshBasicMaterial;
        useSelectedObjectStore.getState().setOriginalMaterial(mesh.material ? material : null);
        useSelectedObjectStore.getState().setSelectedObject(mesh);

        // Update the object info store
        useObjectInfoStore.getState().setName(mesh.name);
        useObjectInfoStore.getState().setFaceIndex(event.faceIndex || 0);
        query_ws_server_mesh_info(mesh.name, event.faceIndex || 0);
        // Create a new material for the selected mesh
        mesh.material = selectedMaterial;

        // Update the tree view selection
        const treeViewStore = useTreeViewStore.getState();
        if (treeViewStore.treeData) {
            const selectedNode = findNodeById(treeViewStore.treeData, mesh.name);
            if (selectedNode) {
                treeViewStore.setSelectedNodeId(selectedNode.id);
            }
        }
    } else {
        deselectObject();
    }

}

export function handleMeshEmptySpace(event: MouseEvent) {
    event.stopPropagation();
    deselectObject();
}


function findNodeById(nodes: TreeNode, id: string): TreeNode | null {
    if (nodes.id === id) {
        return nodes;
    }
    if (Array.isArray(nodes.children)) {
        for (let child of nodes.children) {
            const result = findNodeById(child, id);
            if (result) {
                return result;
            }
        }
    }
    return null;
}