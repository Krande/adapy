// Fitting the camera to a box or to everything loaded. Kept apart from setupCameraControlsHandlers
// (the viewer's key and pointer bindings, which pull in most of the app) so the plugin view verbs
// and their tests can import it.
import * as THREE from "three";
import CameraControls from "camera-controls";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";
import {framePose, wholeSphereDistance} from "@/viewport/api/cameraFrame";
import type {Box6} from "@/viewport/api/sceneFrame";

const box6 = (b: THREE.Box3): Box6 => [b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z];

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
    const pose = framePose(box6(box), camera.getWorldDirection(new THREE.Vector3()).toArray(), camera.fov, camera.aspect, distanceScale, 1e-4);
    if (!pose) return;
    applyAdaptiveClipping(camera, controls, pose.radius);
    const center = new THREE.Vector3(...pose.target);
    const newPosition = new THREE.Vector3(...pose.eye);
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

/** The world box of everything "fit all" frames: the imported, visible meshes, helpers (the grid,
 *  the section caps, the transform gizmo) left out. Null when there is none. */
export const sceneFitBox = (scene: THREE.Object3D): THREE.Box3 | null => {
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
    return hasMesh && !overallBox.isEmpty() ? overallBox : null;
};

/** How far from a sphere's centre the camera must stand for the WHOLE sphere to be in view, both
 *  ways (its silhouette inside the narrower field of view). Farther than zoomToAll's fit, which
 *  puts the sphere's outline at the centre's depth on the edge, so geometry nearer the camera
 *  than the centre can fall outside. */
export const fitDistance = (camera: THREE.PerspectiveCamera, radius: number): number =>
    wholeSphereDistance(radius, camera.fov, camera.aspect);

export const zoomToAll = (scene: THREE.Scene, camera: THREE.PerspectiveCamera, controls: OrbitControls | CameraControls) => {
    // Compute bounding box only from imported/visible meshes, excluding helpers
    // like GridHelper, the section caps/stencil and the transform gizmo.
    const overallBox = sceneFitBox(scene);
    if (!overallBox) return;

    // The shared fit (viewport/api/cameraFrame): the box's sphere, along the current viewing direction.
    const pose = framePose(box6(overallBox), camera.getWorldDirection(new THREE.Vector3()).toArray(), camera.fov, camera.aspect);
    if (!pose) return;

    // Adapt near/far to model size so small models can be zoomed into without near-plane clipping.
    applyAdaptiveClipping(camera, controls, pose.radius);

    const center = new THREE.Vector3(...pose.target);
    const newPosition = new THREE.Vector3(...pose.eye);

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
