// sceneHelpers/addOrientationGizmo.ts
import { OrientationGizmo } from "./OrientationGizmo";
import * as THREE from "three";

export function addOrientationGizmo(
  camera: THREE.Camera,
  container: HTMLElement
): OrientationGizmo {
  // Register once, then construct through whatever is REGISTERED — not through the
  // imported class.
  //
  // Custom-element names cannot be re-registered. On a hot update this module
  // re-evaluates with a fresh `OrientationGizmo` class, `customElements.get` still
  // returns the OLD one, so the define is skipped and the new class is never registered.
  // Constructing it then throws "Failed to construct 'HTMLElement': Illegal constructor",
  // which took ThreeCanvas down to its error boundary — the 3D view went blank after any
  // edit until a full page reload. Dev-only, but it is every dev, every edit.
  if (!customElements.get("orientation-gizmo")) {
    customElements.define("orientation-gizmo", OrientationGizmo);
  }
  const Registered = customElements.get("orientation-gizmo") as typeof OrientationGizmo;

  // The gizmo class now self-positions in connectedCallback (display,
  // size, position:fixed, anchor → top/right/bottom/left). Caller just
  // picks the size, anchor, and margins.
  const isNarrow = window.matchMedia("(max-width: 767px)").matches;
  const size = isNarrow ? 80 : 150;

  const gizmo = new Registered(camera, {
    size,
    bubbleSizePrimary: isNarrow ? 6 : 10,
    bubbleSizeSeconday: isNarrow ? 6 : 10,
    fontSize: isNarrow ? "8px" : "10px",
    anchor: "bottom-right",
    anchorMarginX: 8,
    // Slightly bigger Y margin on phones to clear the Android
    // gesture-nav pill — safe-area-inset-bottom (added on top inside
    // the gizmo) is not reliably populated by Chrome on Android.
    anchorMarginY: isNarrow ? 20 : 8,
  });

  container.appendChild(gizmo);

  return gizmo;
}
