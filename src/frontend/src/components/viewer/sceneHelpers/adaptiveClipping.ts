import * as THREE from "three";
import CameraControls from "camera-controls";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls.js";

// Scale the camera near/far clipping planes (and the control dolly limits) to the size of the
// content the camera is framing, so small models don't clip away when zooming in.
//
// The camera is constructed with a fixed near = 0.1 (setupCamera.ts). That means you can never get
// closer than 0.1 world units before geometry crosses the near plane and disappears — fine for a
// building-scale model, but for a model only a few units across (e.g. a component in mm modelled at
// unit scale) 0.1 clips most of it the moment you try to zoom in. Deriving near from the framed
// bounding-sphere radius makes the zoom-in headroom proportional to model size at any scale.
//
// near/far span keeps a ~1e6 ratio; with far tied to the same radius the model stays comfortably
// inside the frustum (camera fits at ~2*radius) while near shrinks enough to inspect fine detail.
export function applyAdaptiveClipping(
    camera: THREE.PerspectiveCamera,
    controls: OrbitControls | CameraControls | null | undefined,
    radius: number,
) {
    if (!isFinite(radius) || radius <= 0) return;

    const near = Math.max(radius * 1e-3, 1e-6);
    const far = Math.max(radius * 1e3, near * 1e4);

    // Only rewrite when meaningfully different — avoids redundant projection-matrix churn when the
    // same model is re-framed (fit-all, center-on-selection) at an already-correct scale.
    if (Math.abs(camera.near - near) > near * 1e-3 || Math.abs(camera.far - far) > far * 1e-3) {
        camera.near = near;
        camera.far = far;
        camera.updateProjectionMatrix();
    }

    // Let the controls dolly all the way in to the near plane (and not past far). CameraControls and
    // OrbitControls both gate dolly by minDistance/maxDistance; default minDistance keeps you further
    // out than the new near plane would allow, so match them to the adaptive planes.
    if (controls instanceof OrbitControls) {
        controls.minDistance = near;
        controls.maxDistance = far;
    } else if (controls instanceof CameraControls) {
        controls.minDistance = near;
        controls.maxDistance = far;
    }
}
