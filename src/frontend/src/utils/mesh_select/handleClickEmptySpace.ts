import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";

export function handleClickEmptySpace(event: MouseEvent) {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
    selectedObjects.forEach((drawRangeIds, mesh) => {
        mesh.clearSelectionGroups();
    });

}