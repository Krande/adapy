import { CustomBatchedMesh } from "./CustomBatchedMesh";
import { useSelectedObjectStore } from "../../state/useSelectedObjectStore";

export function perform_selection(
  mesh: CustomBatchedMesh,
  shiftKey: boolean,
  rangeId: string
) {
  const selectedObjectStore = useSelectedObjectStore.getState();

  const selectedObjects = selectedObjectStore.selectedObjects;
  const selectedRanges = selectedObjects.get(mesh);
  const isAlreadySelected = selectedRanges
    ? selectedRanges.has(rangeId)
    : false;

  if (shiftKey) {
    if (isAlreadySelected) {
      // If Shift is held and the draw range is already selected, deselect it
      selectedObjectStore.removeSelectedObject(mesh, rangeId);
    } else {
      // If Shift is held and the draw range is not selected, add it to selection
      selectedObjectStore.addSelectedObject(mesh, rangeId);
    }
  } else {
    // Clear the selection if the draw range is already selected
    selectedObjectStore.clearSelectedObjects();

    // Select the new draw range
    selectedObjectStore.addSelectedObject(mesh, rangeId);
  }
}