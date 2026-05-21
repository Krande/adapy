import * as THREE from "three";

/**
 * Shared invariant for every camera that renders an adapy model.
 *
 * Layer 0 is the default — surface meshes, points, anything pickable.
 * Layer 1 is for non-pickable visual overlays the adapy pipeline puts
 * there explicitly: the GLB edge wireframe (LineSegments —
 * `prepareLoadedModel` moves these to layer 1) and the dynamic
 * ground grid (`addDynamicGridHelper`). A camera that doesn't enable
 * layer 1 silently hides those overlays.
 *
 * The pickability separation works because raycasters explicitly
 * restrict to layer 0 (`setupPointerHandler` calls `ray.layers.set(0)`),
 * so even with both camera layers enabled, the picker only touches
 * surface meshes.
 *
 * Anyone constructing a camera (standalone setupCamera, paradoc
 * embed in `embed/index.ts`, FEA result viewers) must call this.
 * Forgetting it is the "edges don't render in the embed" bug.
 */
export function applyStandardLayers(camera: THREE.Camera): void {
  camera.layers.enable(0);
  camera.layers.enable(1);
}

export function setupCamera(
  container: HTMLDivElement,
  zIsUp: boolean,
): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(
    60,
    container.clientWidth / container.clientHeight,
    0.1,
    10000,
  );
  camera.position.set(-5, 5, 5);
  applyStandardLayers(camera);

  const up = zIsUp ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
  camera.up.copy(up);
  camera.updateProjectionMatrix();

  return camera;
}
