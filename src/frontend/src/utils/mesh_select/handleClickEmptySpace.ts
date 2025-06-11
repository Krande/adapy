import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";
import {CustomBatchedMesh} from "./CustomBatchedMesh";
import {Object3D} from "three";

export function handleClickEmptySpace(event: MouseEvent) {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
    selectedObjects.forEach((drawRangeIds, mesh) => {
        if (!(mesh instanceof CustomBatchedMesh)) {
            // loop recursively through the children of the mesh
            (mesh as Object3D).traverse((child: Object3D) => {
                if (child instanceof CustomBatchedMesh) {
                    child.clearSelectionGroups();
                }
            });
        } else {
            mesh.clearSelectionGroups();
        }
    });

}