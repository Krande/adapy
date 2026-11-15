// Named camera views: top, bottom, front, back, left, right and isometric (View -> Camera).
//
// A view looks at the model from one side, framing everything "fit all" frames, with the same
// FOV fit and adaptive clipping (`./cameraFit`) and, on camera-controls, the same smooth
// transition. The directions are in the viewer's world: Z up by default (`zIsUp`, what
// `setupCamera` and `ThreeCanvas` set), else Y up. "Front" looks along +Y in a Z-up world
// (from -Y), the usual drawing convention; the Y-up directions are the same views turned the way
// a Y-up glTF is turned into this viewer's Z-up world ((x, y, z) -> (x, z, -y)).
import * as THREE from "three";
import type CameraControls from "camera-controls";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";
import {orthoFitPose, sceneFitBox} from "@/components/viewer/sceneHelpers/cameraFit";

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
    const target = controls instanceof OrbitControls ? controls.target : controls.getTarget(new THREE.Vector3());
    const towardCamera = namedViewDirection(view, zIsUp);
    const lookAlong = [-towardCamera[0], -towardCamera[1], -towardCamera[2]];
    // In the orthographic view the model's outline on the view plane frames it (cameraFit `orthoFitPose`).
    const pose =
        (box && orthoFitPose(camera, box, undefined, 0, lookAlong, zIsUp ? [0, 0, 1] : [0, 1, 0])) ??
        namedViewPose(
            box ? [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z] : null,
            towardCamera,
            camera.fov,
            camera.aspect,
            {eye: camera.position.toArray(), target: target.toArray()},
        );
    if (pose.radius !== null) applyAdaptiveClipping(camera, controls, pose.radius);
    const [px, py, pz] = pose.eye, [cx, cy, cz] = pose.target;
    if (controls instanceof OrbitControls) {
        camera.position.set(px, py, pz);
        camera.lookAt(cx, cy, cz);
        controls.target.set(cx, cy, cz);
        camera.updateProjectionMatrix();
        controls.update();
    } else {
        void controls.setLookAt(px, py, pz, cx, cy, cz, true);
        camera.updateProjectionMatrix();
    }
}
