// handleClickMesh.ts
import {ThreeEvent} from '@react-three/fiber';
import {useSelectedObjectStore} from '../../state/useSelectedObjectStore';
import {useTreeViewStore} from '../../state/treeViewStore';
import {findNodeById} from '../tree_view/findNodeById';
import {getSelectedMeshDrawRange} from './getSelectedMeshDrawRange';
import {useObjectInfoStore} from '../../state/objectInfoStore';
import {useModelStore} from '../../state/modelStore';
import {CustomBatchedMesh} from './CustomBatchedMesh';

export function handleClickMesh(event: ThreeEvent<PointerEvent>) {
    event.stopPropagation();

    // if right-click, return
    if (event.button === 2) {
        return;
    }

    const mesh = event.object as CustomBatchedMesh;
    const faceIndex = event.faceIndex || 0;
    const shiftKey = event.nativeEvent.shiftKey;

    const translation = useModelStore.getState().translation;

    // Get the 3D coordinates from the click event
    const clickPosition = event.point.clone();

    // Adjust for translation if necessary
    if (translation) {
        clickPosition.sub(translation);
    }

    // Update the object info store
    useObjectInfoStore.getState().setClickCoordinate(clickPosition);

    // Get the draw range for the selected face
    const drawRange = getSelectedMeshDrawRange(mesh, faceIndex);

    if (!drawRange) {
        return;
    }

    const [rangeId, start, count] = drawRange;

    perform_selection(mesh, shiftKey, rangeId);

    // Update object info and tree view selection
    const scene = useModelStore.getState().scene;
    const hierarchy: Record<string, [string, string | number]> = scene?.userData['id_hierarchy'];
    const [nodeName] = hierarchy[rangeId];

    if (nodeName) {
        useObjectInfoStore.getState().setName(nodeName);
        const treeViewStore = useTreeViewStore.getState();
        if (treeViewStore.treeData) {
            const selectedNode = findNodeById(treeViewStore.treeData, nodeName);
            if (selectedNode) {
                treeViewStore.setSelectedNodeId(selectedNode.id);
            }
        }
    }
}

export function perform_selection(mesh: CustomBatchedMesh, shiftKey: boolean, rangeId: string) {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
    const selectedRanges = selectedObjects.get(mesh);
    const isAlreadySelected = selectedRanges ? selectedRanges.has(rangeId) : false;

    if (shiftKey) {
        if (isAlreadySelected) {
            // If Shift is held and the draw range is already selected, deselect it
            useSelectedObjectStore.getState().removeSelectedObject(mesh, rangeId);
        } else {
            // If Shift is held and the draw range is not selected, add it to selection
            useSelectedObjectStore.getState().addSelectedObject(mesh, rangeId);
        }
    } else {
        useSelectedObjectStore.getState().clearSelectedObjects();
        // Select the new draw range
        useSelectedObjectStore.getState().addSelectedObject(mesh, rangeId);
    }
}
