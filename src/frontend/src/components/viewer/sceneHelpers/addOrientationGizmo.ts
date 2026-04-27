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

  // Smaller gizmo on phone-sized viewports — the default 150 takes a
  // sizeable bite out of the visible canvas. Match Tailwind's `md`
  // breakpoint (768px).
  const isNarrow = window.matchMedia("(max-width: 767px)").matches;
  const size = isNarrow ? 80 : 150;

  const gizmo = new OrientationGizmo(camera, {
    size,
    bubbleSizePrimary: isNarrow ? 6 : 10,
    bubbleSizeSeconday: isNarrow ? 6 : 10,
    fontSize: isNarrow ? "8px" : "10px",
  });
  gizmo.style.position = "absolute";
  // Respect iOS safe-area insets so the gizmo isn't clipped under the
  // home-indicator / notch in landscape.
  gizmo.style.bottom = "max(8px, env(safe-area-inset-bottom, 0px))";
  gizmo.style.right = "max(8px, env(safe-area-inset-right, 0px))";
  gizmo.style.pointerEvents = "auto";
  gizmo.style.zIndex = "10";

  container.appendChild(gizmo);

  return gizmo;
}
