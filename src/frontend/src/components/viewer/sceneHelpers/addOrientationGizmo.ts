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

  // The gizmo class now self-positions in connectedCallback (display,
  // size, position:fixed, anchor → top/right/bottom/left). Caller just
  // picks the size, anchor, and margins.
  const isNarrow = window.matchMedia("(max-width: 767px)").matches;
  const size = isNarrow ? 80 : 150;

  const gizmo = new OrientationGizmo(camera, {
    size,
    bubbleSizePrimary: isNarrow ? 6 : 10,
    bubbleSizeSeconday: isNarrow ? 6 : 10,
    fontSize: isNarrow ? "8px" : "10px",
    anchor: "bottom-right",
    anchorMarginX: 8,
    // Bigger Y margin on phones to clear Android gesture-nav pill —
    // safe-area-inset-bottom (added on top inside the gizmo) is not
    // reliably populated by Chrome on Android.
    anchorMarginY: isNarrow ? 36 : 8,
  });

  container.appendChild(gizmo);

  return gizmo;
}
