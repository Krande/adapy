import { useSelectedObjectStore } from "@/state/useSelectedObjectStore";
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

  // ``additiveMode`` lets mobile users get the same behaviour
  // shiftKey unlocks on desktop, without needing a Shift key. The
  // toggle that flips it lives in the Object Info panel and is
  // mobile-only, so on desktop the OR-clause stays harmless.
  const additive = shiftKey || selectedObjectStore.additiveMode;
  if (additive) {
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