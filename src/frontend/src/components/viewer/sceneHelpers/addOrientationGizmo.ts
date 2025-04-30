// sceneHelpers/addOrientationGizmo.ts
import { OrientationGizmo } from "./OrientationGizmo";
import * as THREE from "three";

export function addOrientationGizmo(
  camera: THREE.Camera,
  container: HTMLElement
): OrientationGizmo {
  if (!customElements.get("orientation-gizmo")) {
    customElements.define("orientation-gizmo", OrientationGizmo);
  }

  const gizmo = new OrientationGizmo(camera, {});
  gizmo.style.position = "absolute";
  gizmo.style.bottom = "8px";
  gizmo.style.right = "8px";
  gizmo.style.pointerEvents = "auto";
  gizmo.style.zIndex = "10";

  container.appendChild(gizmo);

  return gizmo;
}
