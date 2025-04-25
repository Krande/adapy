// sceneHelpers/setupCameraControlsHandlers.ts
import * as THREE from "three";
import { useSelectedObjectStore } from "../../../state/useSelectedObjectStore";
import { CustomBatchedMesh } from "../../../utils/mesh_select/CustomBatchedMesh";
import { centerViewOnSelection } from "../../../utils/scene/centerViewOnSelection";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls";

export function setupCameraControlsHandlers(
  scene: THREE.Scene,
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
) {
  const zoomToAll = () => {
    const box = new THREE.Box3().setFromObject(scene);
    const size = box.getSize(new THREE.Vector3()).length();
    const center = box.getCenter(new THREE.Vector3());

    const scale = 0.5;
    camera.position.set(
      center.x + size * scale,
      center.y + size * scale,
      center.z + size * scale,
    );
    camera.lookAt(center);
    controls.target.copy(center);
    controls.update();
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    const key = event.key.toLowerCase();
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;

    if (event.shiftKey && key === "h") {
      selectedObjects.forEach((drawRangeIds, mesh) => {
        drawRangeIds.forEach((drawRangeId) => {
          mesh.hideDrawRange(drawRangeId);
        });
        mesh.deselect();
      });
      useSelectedObjectStore.getState().clearSelectedObjects();
    } else if (event.shiftKey && key === "u") {
      scene.traverse((obj) => {
        if (obj instanceof CustomBatchedMesh) {
          obj.unhideAllDrawRanges();
        }
      });
    } else if (event.shiftKey && key === "f") {
      centerViewOnSelection(controls, camera);
    } else if (event.shiftKey && key === "a") {
      zoomToAll();
    }
  };

  window.addEventListener("keydown", handleKeyDown);

  return () => {
    window.removeEventListener("keydown", handleKeyDown);
  };
}
