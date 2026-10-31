// Fitting the camera to a box or to everything loaded. Kept apart from setupCameraControlsHandlers
// (the viewer's key and pointer bindings, which pull in most of the app) so the plugin view verbs
// and their tests can import it.
import * as THREE from "three";
import CameraControls from "camera-controls";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";

// Skip helper subtrees (section planes, caps, the transform gizmo) and hidden
// objects when fitting the camera. setFromObject ignores `visible`, so the
// gizmo — which scales with camera distance — would otherwise blow up the box
// and fling the camera off into empty space (the "solid grey" screen).
export const isExcludedFromFit = (obj: THREE.Object3D): boolean => {
    let cur: THREE.Object3D | null = obj;
    while (cur) {
        if (!cur.visible || cur.userData?.__excludeFromFit) return true;
        cur = cur.parent;
    }
    return false;
};

// Frame the camera on an explicit world-space box (a single face, a sub-selection) reusing the same
// FOV-based sphere fit as zoomToAll. distanceScale > 1 leaves breathing room around a tiny target so a
// small face doesn't fill the whole viewport with the near surface.
export const frameBox = (
    box: THREE.Box3,
    camera: THREE.PerspectiveCamera,
    controls: OrbitControls | CameraControls,
    distanceScale = 1.6,
) => {
    if (box.isEmpty()) return;
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    if (!sphere || !isFinite(sphere.radius) || sphere.radius === 0) return;
    const center = sphere.center.clone();
    const radius = Math.max(sphere.radius, 1e-4);
    applyAdaptiveClipping(camera, controls, radius);
    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const aspect = camera.aspect || 1;
    const vDist = radius / Math.tan(vFov / 2);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
    const hDist = radius / Math.tan(hFov / 2);
    const distance = Math.max(vDist, hDist) * distanceScale;
    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction).normalize();
    const newPosition = center.clone().add(direction.clone().multiplyScalar(-distance));
    if (controls instanceof OrbitControls) {
        camera.position.copy(newPosition);
        camera.lookAt(center);
        controls.target.copy(center);
        camera.updateProjectionMatrix();
        controls.update();
    } else if (controls instanceof CameraControls) {
        controls.setLookAt(newPosition.x, newPosition.y, newPosition.z, center.x, center.y, center.z, true);
        camera.updateProjectionMatrix();
    }
};

export const zoomToAll = (scene: THREE.Scene, camera: THREE.PerspectiveCamera, controls: OrbitControls | CameraControls) => {
    // Compute bounding box only from imported/visible meshes, excluding helpers
    // like GridHelper, the section caps/stencil and the transform gizmo.
    const overallBox = new THREE.Box3();
    let hasMesh = false;

    scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh && !isExcludedFromFit(obj)) {
            const objBox = new THREE.Box3().setFromObject(obj);
            if (!objBox.isEmpty() && isFinite(objBox.min.x) && isFinite(objBox.max.x)) {
                overallBox.union(objBox);
                hasMesh = true;
            }
        }
    });

    if (!hasMesh || overallBox.isEmpty()) return;

    // Compute a bounding sphere from the overall box for robust FOV-based fitting
    const sphere = overallBox.getBoundingSphere(new THREE.Sphere());
    if (!sphere || sphere.radius === 0 || !isFinite(sphere.radius)) return;

    const center = sphere.center.clone();
    const radius = sphere.radius;

    // Adapt near/far to model size so small models can be zoomed into without near-plane clipping.
    applyAdaptiveClipping(camera, controls, radius);

    // Compute required distance so the sphere fits both vertically and horizontally
    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const aspect = camera.aspect || 1;
    const vDist = radius / Math.tan(vFov / 2);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
    const hDist = radius / Math.tan(hFov / 2);
    const distance = Math.max(vDist, hDist);

    // Move the camera back along its current viewing direction
    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction).normalize();
    const newPosition = center.clone().add(direction.clone().multiplyScalar(-distance));

    if (controls instanceof OrbitControls) {
        camera.position.copy(newPosition);
        camera.lookAt(center);
        controls.target.copy(center);
    } else if (controls instanceof CameraControls) {
        controls.setLookAt(
            newPosition.x, newPosition.y, newPosition.z,
            center.x, center.y, center.z,
            true // enable smooth transition
        );
    }

    camera.updateProjectionMatrix();
    if (controls instanceof OrbitControls) {
        controls.update();
    }
};
