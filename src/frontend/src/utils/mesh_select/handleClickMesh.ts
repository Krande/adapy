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
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {useModelStore} from "../../state/modelStore";

export function handleClickMesh(event: ThreeEvent<PointerEvent>) {
    event.stopPropagation();

    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const mesh = event.object as THREE.Mesh;
    const face_index = event.faceIndex || 0;


    if (face_index == useObjectInfoStore.getState().faceIndex && (mesh == selectedObject)) {
        deselectObject();
        useObjectInfoStore.getState().setFaceIndex(null);
        return;
    }

    useObjectInfoStore.getState().setFaceIndex(face_index);
    let drawRange = getSelectedMeshDrawRange(mesh, face_index);

    if (!drawRange) {
        return null;
    }

    highlightDrawRange(mesh, drawRange)

    const [rangeId, start, count] = drawRange;

    let scene = useModelStore.getState().scene;
    let hierarchy: Record<string, [string, string | number]> = scene?.userData["id_hierarchy"];
    const [node_name, parent_node_name] = hierarchy[rangeId];
    if (node_name) {
        // Update the object info store
        useObjectInfoStore.getState().setName(node_name);
    }

    // Update the tree view selection
    const treeViewStore = useTreeViewStore.getState();
    if (treeViewStore.treeData) {
        const selectedNode = findNodeById(treeViewStore.treeData, node_name);
        if (selectedNode) {
            treeViewStore.setSelectedNodeId(selectedNode.id);
        }
    }


}