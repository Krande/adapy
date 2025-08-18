import { useSelectedObjectStore } from "../../state/useSelectedObjectStore";
import { Object3D } from "three";

export async function perform_selection(
  obj: Object3D,
  shiftKey: boolean,
  rangeId: string
) {
  const selectedObjectStore = useSelectedObjectStore.getState();

  const selectedObjects = selectedObjectStore.selectedObjects;
  const selectedRanges = selectedObjects.get(obj);
  const isAlreadySelected = selectedRanges
    ? selectedRanges.has(rangeId)
    : false;

  if (shiftKey) {
    if (isAlreadySelected) {
      // If Shift is held and the draw range is already selected, deselect it
      selectedObjectStore.removeSelectedObject(obj, rangeId);
    } else {
      // If Shift is held and the draw range is not selected, add it to selection
      selectedObjectStore.addSelectedObject(obj, rangeId);
    }
  } else {
    // Clear the selection if the draw range is already selected
    selectedObjectStore.clearSelectedObjects();

    // Select the new draw range
    selectedObjectStore.addSelectedObject(obj, rangeId);
  }
}