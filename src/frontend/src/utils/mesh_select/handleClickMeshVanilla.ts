import * as THREE from "three";
import { handleMeshSelectionCore } from "./handleMeshSelectionCore";

export function handleClickMeshVanilla(
  intersect: THREE.Intersection,
  event: MouseEvent
) {
  if (event.button === 2) return;

  handleMeshSelectionCore({
    object: intersect.object,
    faceIndex: intersect.faceIndex ?? undefined,
    point: intersect.point,
    shiftKey: event.shiftKey,
  });
}
