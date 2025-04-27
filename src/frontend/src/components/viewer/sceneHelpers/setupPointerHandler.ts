import * as THREE from "three";
import { handleClickEmptySpace } from "../../../utils/mesh_select/handleClickEmptySpace";
import { handleClickMeshVanilla } from "../../../utils/mesh_select/handleClickMeshVanilla";

export function setupPointerHandler(
  container: HTMLDivElement,
  camera: THREE.PerspectiveCamera,
  scene: THREE.Scene,
  renderer: THREE.WebGLRenderer,
) {
  const onClick = (event: MouseEvent) => {
    const rect = renderer.domElement.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    const pointer = new THREE.Vector2(x, y);

    const raycaster = new THREE.Raycaster();
    raycaster.layers.set(0);
    raycaster.layers.disable(1);
    raycaster.setFromCamera(pointer, camera);

    const intersects = raycaster.intersectObjects(scene.children, true);

    if (intersects.length === 0) {
      handleClickEmptySpace(event);
    } else {
      handleClickMeshVanilla(intersects[0], event);
    }
  };

  renderer.domElement.addEventListener("pointerdown", onClick);

  return () => {
    renderer.domElement.removeEventListener("pointerdown", onClick);
  };
}
