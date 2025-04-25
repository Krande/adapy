// handleClickMesh.ts
import { ThreeEvent } from "@react-three/fiber";
import { handleMeshSelectionCore } from "../../../utils/mesh_select/handleMeshSelectionCore";

export function handleClickMeshFiber(event: ThreeEvent<PointerEvent>) {
  event.stopPropagation();
  if (event.button === 2) return;

  handleMeshSelectionCore({
    object: event.object,
    faceIndex: event.faceIndex ?? undefined,
    point: event.point,
    shiftKey: event.nativeEvent.shiftKey,
  });
}

