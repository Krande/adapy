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
  // Clear Android's gesture-nav pill (Chrome on Android often reports
  // safe-area-inset-bottom as 0 even with viewport-fit=cover, so we
  // need an explicit floor on mobile). 36px clears the standard pill;
  // safe-area-inset-bottom adds on top for iOS home-indicator.
  const bottomFloor = isNarrow ? 36 : 8;
  const sideFloor = 8;
  gizmo.style.bottom = `calc(env(safe-area-inset-bottom, 0px) + ${bottomFloor}px)`;
  gizmo.style.right = `calc(env(safe-area-inset-right, 0px) + ${sideFloor}px)`;
  gizmo.style.pointerEvents = "auto";
  gizmo.style.zIndex = "10";

  container.appendChild(gizmo);

  return gizmo;
}
