import {ThreeEvent} from "@react-three/fiber";
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import * as THREE from "three";
import {deselectObject} from "./deselectObject";
import {setSelectedMesh} from "./setSelectedMesh";
import {useTreeViewStore} from "../../state/treeViewStore";
import {findNodeById} from "../tree_view/findNodeById";
import {getSelectedMeshDrawRange} from "./getSelectedMeshDrawRange";
import {highlightDrawRange} from "./highlightDrawRange";
import {selectedMaterial} from "../default_materials";

export function handleClickMesh(event: ThreeEvent<PointerEvent>) {
    event.stopPropagation();

    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const mesh = event.object as THREE.Mesh;

    if (selectedObject !== mesh) {

        // setSelectedMesh(mesh, event.faceIndex || 0);

        let draw_range = getSelectedMeshDrawRange(mesh, event.faceIndex || 0);

        if (draw_range){
            highlightDrawRange(mesh, draw_range)
        }
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