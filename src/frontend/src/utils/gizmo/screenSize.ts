/**
 * A gizmo kept ONE SIZE ON SCREEN, in perspective and drawn orthographically.
 *
 * three.js's TransformControls scales its arrows by the distance from the eye to the gizmo, which is
 * right for a perspective view. The orthographic view here is the same PerspectiveCamera with an
 * orthographic projection (utils/scene/orthographicProjection.ts), as tall as the perspective view is
 * at the controls' TARGET: the gizmo's distance says nothing about that height. So the arrows grew and
 * shrank as the view orbited and zoomed (a box far behind the target: large; in front of it: tiny).
 * Here the gizmo's `size` is set, just before it scales itself on each draw, to what makes its arrows
 * cover the same share of the view's height wherever the gizmo is and however the view is drawn.
 *
 * The builder's resize handles (spheres at a box's face centres) are sized the same way:
 * `screenSizedScale` gives the scale that makes a unit sphere that share of the view's height.
 */

import * as THREE from "three";
import type {TransformControls} from "three/examples/jsm/controls/TransformControls";
import {viewHeightAt} from "@/utils/scene/orthographicProjection";

const eye = new THREE.Vector3();

/** The factor TransformControls scales its handles by (its own formula, three 0.176), times 4 / size. */
export function transformControlsFactor(camera: THREE.Camera, point: THREE.Vector3): number {
    if (camera instanceof THREE.OrthographicCamera) return (camera.top - camera.bottom) / camera.zoom;
    if (!(camera instanceof THREE.PerspectiveCamera)) return 1;
    const distance = camera.getWorldPosition(eye).distanceTo(point);
    return distance * Math.min((1.9 * Math.tan((Math.PI * camera.fov) / 360)) / camera.zoom, 7);
}

/** In a plain perspective view, TransformControls' factor is this share of the view's height. */
const PERSPECTIVE_SHARE = 1.9 / 2;

/**
 * The TransformControls `size` that draws its handles as large on screen as `base` does in a plain
 * perspective view, for a gizmo at `point` (world space).
 */
export function gizmoSizeFor(camera: THREE.Camera, point: THREE.Vector3, base = 1): number {
    if (camera instanceof THREE.OrthographicCamera) return base;
    const factor = transformControlsFactor(camera, point);
    const height = viewHeightAt(camera, point);
    if (!(factor > 0) || !(height > 0) || !Number.isFinite(factor) || !Number.isFinite(height)) return base;
    return (base * PERSPECTIVE_SHARE * height) / factor;
}

/** The scale that makes something one unit large cover `share` of the view's height at `point`. */
export function screenSizedScale(camera: THREE.Camera, point: THREE.Vector3, share: number): number {
    const height = viewHeightAt(camera, point);
    return height > 0 && Number.isFinite(height) ? height * share : share;
}

const at = new THREE.Vector3();

/**
 * Keep `controls` one size on screen: its `size` is set from the camera it draws for each time its
 * helper is brought up to date (in the renderer's pass over the scene, after the camera moved), before
 * it scales its handles. Returns the undo.
 */
export function keepScreenSized(controls: TransformControls, base = 1): () => void {
    const helper = controls.getHelper();
    const own = helper.updateMatrixWorld;
    helper.updateMatrixWorld = function (this: THREE.Object3D, force?: boolean) {
        const object = controls.object;
        if (object && controls.camera) {
            object.getWorldPosition(at);
            controls.size = gizmoSizeFor(controls.camera, at, base);
        }
        own.call(this, force);
    };
    return () => {
        helper.updateMatrixWorld = own;
    };
}
