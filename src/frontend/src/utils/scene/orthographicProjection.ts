// An orthographic view on the three.js viewport's one camera.
//
// The viewport builds a PerspectiveCamera once, and much is built around that object: the
// controls, the gizmos, the pointer handlers, the fit and named-view framing (which place the
// camera at a distance from its target for a field of view). So the orthographic view does not
// swap the camera: the same camera gets an orthographic PROJECTION, as tall at the controls'
// target as the perspective view is there (2 * distance * tan(fov / 2)). Switching therefore
// keeps the view direction, the target and the framing; and everything that moves the camera
// keeps working as it did - zoom (a dolly) changes the distance and so the height, a pan moves by
// the same amount. A fit or a named view stands the camera at the distance that gives the height
// the box's outline on the view plane needs (viewport/api/cameraFrame `orthoFramePose`, through
// cameraFit `orthoFitPose`): a perspective fit's distance would frame loosely here.
//
// What a projection alone does not carry, done here:
//  - the projection follows the distance every frame (a dolly does not ask for it). The frame
//    loop asks for it before either renderer draws (`beforeDraw`), not from inside a
//    WebGLRenderer call: the adacpp WebGPU renderer draws without one;
//  - a ray from the pointer is parallel to the view, not from the eye (Raycaster);
//  - a sprite drawn at a constant screen size (`sizeAttenuation: false`: labels, snap markers,
//    the cell builder's readouts) is sized from its depth by the shader in perspective only, so
//    it is given that size here instead. adacpp's sprite shader does the same as three.js, so
//    SceneSync gives the sprites it hands over that size too (viewport/adacpp/sceneSync.ts).

import * as THREE from "three";
import {followProjection} from "@/state/projectionStore";

/** Half the height the view shows at `distance` with a vertical field of view of `fovDeg`. */
export function orthoHalfHeight(distance: number, fovDeg: number): number {
    return distance * Math.tan((fovDeg * Math.PI) / 360);
}

const ORTHO = "__orthographic";
const DISTANCE = "__orthoDistance";

/** Whether `camera` draws orthographically now (an OrthographicCamera, or ours in that mode). */
export function isOrthographic(camera: THREE.Camera | null | undefined): boolean {
    if (!camera) return false;
    if ("isOrthographicCamera" in camera && camera.isOrthographicCamera === true) return true;
    return camera.userData?.[ORTHO] === true;
}

const eyeAt = new THREE.Vector3();

/**
 * How tall the view is, in model units, at `point` (world space): what one screen height covers there.
 * In perspective that grows with the point's distance from the eye; drawn orthographically it is one
 * height everywhere, the one the projection was made for (from the distance to the controls' target,
 * not to the point). Something kept a constant size on screen is sized from this.
 */
export function viewHeightAt(camera: THREE.Camera, point: THREE.Vector3): number {
    if (camera instanceof THREE.OrthographicCamera) return (camera.top - camera.bottom) / camera.zoom;
    if (!(camera instanceof THREE.PerspectiveCamera)) return 1;
    const tan = Math.tan((camera.fov * Math.PI) / 360);
    if (camera.userData?.[ORTHO] === true) {
        const known: unknown = camera.userData[DISTANCE];
        const distance = typeof known === "number" ? known : camera.getWorldPosition(eyeAt).distanceTo(point);
        return (2 * Math.max(distance, 1e-3) * tan) / camera.zoom;
    }
    return (2 * camera.getWorldPosition(eyeAt).distanceTo(point) * tan) / camera.zoom;
}

/** The camera's orthographic projection for a target `distance` away (or its perspective one). */
export function updateProjection(camera: THREE.PerspectiveCamera, orthographic: boolean, distance: number): void {
    camera.userData[ORTHO] = orthographic;
    if (!orthographic) {
        THREE.PerspectiveCamera.prototype.updateProjectionMatrix.call(camera);
        return;
    }
    const d = Math.max(distance, 1e-3);
    camera.userData[DISTANCE] = d;
    const h = orthoHalfHeight(d, camera.fov) / camera.zoom;
    const w = h * camera.aspect;
    // Symmetric depth range about the camera: nothing between the eye and the target is cut
    // off (an orthographic view has no eye), and depth stays linear.
    camera.projectionMatrix.makeOrthographic(-w, w, h, -h, -camera.far, camera.far);
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
}

let isRaycasterPatched = false;

/** Rays from the pointer through an orthographic view of our camera: parallel, from far behind.
 *  Also for a camera kept in step with another viewport's (the cell builder's on the three.js-free
 *  one), which no `bindOrthographic` ever patched for. */
export function patchRaycaster(): void {
    if (isRaycasterPatched) return;
    isRaycasterPatched = true;
    const original = THREE.Raycaster.prototype.setFromCamera;
    THREE.Raycaster.prototype.setFromCamera = function (this: THREE.Raycaster, coords: THREE.Vector2, camera: THREE.Camera) {
        if (camera instanceof THREE.PerspectiveCamera && camera.userData?.[ORTHO] === true) {
            this.ray.origin.set(coords.x, coords.y, -1).unproject(camera);
            this.ray.direction.set(0, 0, -1).transformDirection(camera.matrixWorld);
            this.camera = camera;
            return;
        }
        original.call(this, coords, camera);
    };
}

const BASE_SCALE = "__orthoBaseScale";
const view = new THREE.Vector3();

/**
 * A constant-screen-size sprite, drawn by an orthographic view of our camera: as large as it
 * is in perspective (the shader multiplies its scale by its depth there, and not here).
 */
export function sizeSpriteForView(sprite: THREE.Sprite, camera: THREE.Camera): void {
    const stored: unknown = sprite.userData[BASE_SCALE];
    const base = stored instanceof THREE.Vector3 ? stored : undefined;
    const isScreenSized = camera.userData?.[ORTHO] === true && sprite.material.sizeAttenuation === false;
    if (!isScreenSized) {
        if (base) {
            sprite.scale.copy(base);
            delete sprite.userData[BASE_SCALE];
            sprite.updateMatrixWorld();
        }
        return;
    }
    const from = base ?? sprite.scale.clone();
    sprite.userData[BASE_SCALE] = from;
    // In perspective it covers scale / tan(fov / 2) of the half-height wherever it is; here the
    // half-height is distance * tan(fov / 2): so scale * distance.
    view.setFromMatrixPosition(sprite.matrixWorld);
    const known: unknown = camera.userData[DISTANCE];
    const distance = typeof known === "number" ? known : camera.position.distanceTo(view);
    sprite.scale.set(from.x * distance, from.y * distance, from.z);
    sprite.updateMatrixWorld();
}

let areSpritesPatched = false;

function patchSprites(): void {
    if (areSpritesPatched) return;
    areSpritesPatched = true;
    const own = THREE.Sprite.prototype.onBeforeRender;
    THREE.Sprite.prototype.onBeforeRender = function (this: THREE.Sprite, renderer, scene, camera, geometry, material, group) {
        sizeSpriteForView(this, camera);
        own.call(this, renderer, scene, camera, geometry, material, group);
    };
}

/** What the controls orbit round: OrbitControls' `target`, or camera-controls' `getTarget`. */
export function controlsTarget(controls: unknown): THREE.Vector3 | null {
    if (typeof controls !== "object" || controls === null) return null;
    if ("target" in controls && controls.target instanceof THREE.Vector3) return controls.target;
    if (!("getTarget" in controls) || typeof controls.getTarget !== "function") return null;
    const target: unknown = controls.getTarget(new THREE.Vector3());
    return target instanceof THREE.Vector3 ? target : null;
}

/**
 * Let `camera` draw orthographically while `orthographic()` says so, round `target()`.
 * Returns the undo (the camera back to perspective).
 */
export function bindOrthographic(
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
    target: () => THREE.Vector3 | null,
    orthographic: () => boolean,
): () => void {
    patchRaycaster();
    patchSprites();
    /** What the projection was last made from: the mode, the distance and the camera's own settings. */
    let made: number[] = [];
    const inputs = (): number[] => {
        const t = target();
        const distance = t ? camera.position.distanceTo(t) : camera.position.length();
        return [orthographic() ? 1 : 0, distance, camera.fov, camera.aspect, camera.zoom, camera.far];
    };
    const apply = () => {
        made = inputs();
        updateProjection(camera, made[0] === 1, made[1]);
    };
    // Whoever asks for the projection (a resize, a fit, the clip range) gets the right one.
    const own = camera.updateProjectionMatrix;
    camera.updateProjectionMatrix = apply;
    // And every frame, before the renderer reads it: a dolly moves the camera without asking. Only
    // when something it is made from has changed: a render that narrows the projection on purpose
    // (the GPU mesh picker's pick matrix, round the clicked pixel) keeps it. Made again on every
    // render, the pick drew the whole view into its one pixel, and every click in an orthographic
    // view took what was at the middle of the screen.
    const before = scene.onBeforeRender;
    scene.onBeforeRender = function (this: THREE.Scene, ...args: Parameters<THREE.Scene["onBeforeRender"]>) {
        if (args[2] === camera && (orthographic() || camera.userData[ORTHO] === true)) {
            const now = inputs();
            if (now.some((v, i) => v !== made[i])) apply();
        }
        before.apply(this, args);
    };
    apply();
    return () => {
        camera.updateProjectionMatrix = own;
        scene.onBeforeRender = before;
        updateProjection(camera, false, 0);
    };
}

/** The orthographic view bound to a viewport: what its frame loop calls, and the undo. */
export interface OrthographicView {
    /** Each frame, after the controls moved the camera and before either renderer draws. */
    beforeDraw(): void;
    release(): void;
}

/**
 * The three.js viewport's camera following the projection setting (state/projectionStore):
 * switched when the setting changes (then `onChange`, to draw a frame), and kept in step with
 * the camera's distance by `beforeDraw` on every frame, whichever renderer draws it.
 */
export function installOrthographicView(
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
    target: () => THREE.Vector3 | null,
    onChange: () => void,
): OrthographicView {
    let isOn = false;
    const release = bindOrthographic(scene, camera, target, () => isOn);
    const stopFollowing = followProjection((on) => {
        isOn = on;
        camera.updateProjectionMatrix();
        onChange();
    });
    return {
        beforeDraw: () => {
            if (isOn || camera.userData[ORTHO] === true) camera.updateProjectionMatrix();
        },
        release: () => {
            stopFollowing();
            release();
        },
    };
}
