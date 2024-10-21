import * as THREE from 'three';
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {query_ws_server_mesh_info} from "./comms/send_mesh_selected_info_callback";
import {defaultMaterial, selectedMaterial} from "../default_materials";
import {useModelStore} from "../../state/modelStore";


export function setSelectedMesh(mesh: THREE.Mesh, faceIndex: number) {
    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalMaterial = useSelectedObjectStore.getState().originalMaterial;
    if (selectedObject) {
        selectedObject.material = originalMaterial ? originalMaterial : defaultMaterial;
    }
    const material = mesh.material as THREE.MeshBasicMaterial;
    useSelectedObjectStore.getState().setOriginalMaterial(mesh.material ? material : null);
    useSelectedObjectStore.getState().setSelectedObject(mesh);

    // Update the object info store
    useObjectInfoStore.getState().setName(mesh.name);
    useObjectInfoStore.getState().setFaceIndex(faceIndex);

    query_ws_server_mesh_info(mesh.name, faceIndex);

    // Create a new material for the selected mesh
    mesh.material = selectedMaterial;
}