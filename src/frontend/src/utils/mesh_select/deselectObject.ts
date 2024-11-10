import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";


export function deselectObject() {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
    selectedObjects.forEach((drawRangeIds, mesh) => {
        mesh.deselect();
    });
    useSelectedObjectStore.getState().clearSelectedObjects();
}




