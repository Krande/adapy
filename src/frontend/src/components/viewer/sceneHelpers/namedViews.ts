// Named camera views: top, bottom, front, back, left, right and isometric (View -> Camera).
//
// A view looks at the model from one side, framing everything "fit all" frames, with the same
// FOV fit and adaptive clipping (`./cameraFit`) and, on camera-controls, the same smooth
// transition. The directions are in the viewer's world: Z up by default (`zIsUp`, what
// `setupCamera` and `ThreeCanvas` set), else Y up. "Front" looks along +Y in a Z-up world
// (from -Y), the usual drawing convention; the Y-up directions are the same views turned the way
// a Y-up glTF is turned into this viewer's Z-up world ((x, y, z) -> (x, z, -y)).
import * as THREE from "three";
import CameraControls from "camera-controls";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";
import {sceneFitBox} from "@/components/viewer/sceneHelpers/cameraFit";

import {CAMERA_VIEWS, type CameraView} from "@/layout/coreCommandIds";
import {namedViewDirection} from "@/utils/camera/namedViewDirection";
import {namedViewPose} from "@/viewport/api/cameraFrame";

export {namedViewDirection};

export const NAMED_VIEWS = CAMERA_VIEWS;
export type NamedView = CameraView;

/** Point the camera at the model from `view`'s side and frame it (the shared rule,
 *  viewport/api/cameraFrame `namedViewPose`). With nothing loaded the camera turns about its
 *  current target at its current distance. */
export function applyNamedView(
    view: NamedView,
    scene: THREE.Object3D,
    camera: THREE.PerspectiveCamera,
    controls: OrbitControls | CameraControls,
    zIsUp = true,
): void {
    const box = sceneFitBox(scene);
    const target = controls instanceof CameraControls ? controls.getTarget(new THREE.Vector3()) : controls.target;
    const pose = namedViewPose(
        box ? [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z] : null,
        namedViewDirection(view, zIsUp),
        camera.fov,
        camera.aspect,
        {eye: camera.position.toArray(), target: target.toArray()},
    );
    if (pose.radius !== null) applyAdaptiveClipping(camera, controls, pose.radius);
    const [px, py, pz] = pose.eye, [cx, cy, cz] = pose.target;
    if (controls instanceof CameraControls) {
        void controls.setLookAt(px, py, pz, cx, cy, cz, true);
        camera.updateProjectionMatrix();
    } else {
        camera.position.set(px, py, pz);
        camera.lookAt(cx, cy, cz);
        controls.target.set(cx, cy, cz);
        camera.updateProjectionMatrix();
        controls.update();
    }
}
