import {ThreeEvent} from "@react-three/fiber";
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import * as THREE from "three";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {query_ws_server_mesh_info} from "./comms/send_mesh_selected_info_callback";
import {useTreeViewStore} from "../../state/treeViewStore";
import {findNodeById} from "../tree_view/findNodeById";
import {deselectObject} from "./deselectObject";
import {defaultMaterial} from "../default_materials";

const selectedMaterial = new THREE.MeshStandardMaterial({color: 'blue', side: THREE.DoubleSide});

export function handleClickMesh(event: ThreeEvent<PointerEvent>) {
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