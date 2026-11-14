// An orthographic view on the three.js viewport's one camera.
//
// The viewport builds a PerspectiveCamera once, and much is built around that object: the
// controls, the gizmos, the pointer handlers, the fit and named-view framing (which place the
// camera at a distance from its target for a field of view). So the orthographic view does not
// swap the camera: the same camera gets an orthographic PROJECTION, as tall at the controls'
// target as the perspective view is there (2 * distance * tan(fov / 2)). Switching therefore
// keeps the view direction, the target and the framing; and everything that moves the camera
// keeps working as it did - zoom (a dolly) changes the distance and so the height, a fit or a
// named view frames as before, a pan moves by the same amount.
//
// What a projection alone does not carry, done here:
//  - the projection follows the distance every frame (a dolly does not ask for it);
//  - a ray from the pointer is parallel to the view, not from the eye (Raycaster);
//  - a sprite drawn at a constant screen size (`sizeAttenuation: false`: labels, snap markers,
//    the cell builder's readouts) is sized from its depth by the shader in perspective only, so
//    it is given that size here instead.

import * as THREE from "three";

/** Half the height the view shows at `distance` with a vertical field of view of `fovDeg`. */
export function orthoHalfHeight(distance: number, fovDeg: number): number {
    return distance * Math.tan((fovDeg * Math.PI) / 360);
}

const ORTHO = "__orthographic";
const DISTANCE = "__orthoDistance";

/** Whether `camera` draws orthographically now (an OrthographicCamera, or ours in that mode). */
export function isOrthographic(camera: THREE.Camera | null | undefined): boolean {
    if (!camera) return false;
    return (camera as THREE.OrthographicCamera).isOrthographicCamera === true || camera.userData?.[ORTHO] === true;
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

let raycastPatched = false;

/** Rays from the pointer through an orthographic view of our camera: parallel, from far behind. */
function patchRaycaster(): void {
    if (raycastPatched) return;
    raycastPatched = true;
    const original = THREE.Raycaster.prototype.setFromCamera;
    THREE.Raycaster.prototype.setFromCamera = function (this: THREE.Raycaster, coords: THREE.Vector2, camera: THREE.Camera) {
        if ((camera as THREE.PerspectiveCamera).isPerspectiveCamera && camera.userData?.[ORTHO] === true) {
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
    const material = sprite.material as THREE.SpriteMaterial;
    const base = sprite.userData[BASE_SCALE] as THREE.Vector3 | undefined;
    const ortho = camera.userData?.[ORTHO] === true && material.sizeAttenuation === false;
    if (!ortho) {
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
    const distance = (camera.userData[DISTANCE] as number | undefined) ?? camera.position.distanceTo(view);
    sprite.scale.set(from.x * distance, from.y * distance, from.z);
    sprite.updateMatrixWorld();
}

let spritesPatched = false;

function patchSprites(): void {
    if (spritesPatched) return;
    spritesPatched = true;
    const own = THREE.Sprite.prototype.onBeforeRender;
    THREE.Sprite.prototype.onBeforeRender = function (this: THREE.Sprite, renderer, scene, camera, geometry, material, group) {
        sizeSpriteForView(this, camera);
        own.call(this, renderer, scene, camera, geometry, material, group);
    };
}

/** What the controls orbit round: OrbitControls' `target`, or camera-controls' `getTarget`. */
export function controlsTarget(controls: unknown): THREE.Vector3 | null {
    const c = controls as {target?: THREE.Vector3; getTarget?: (out: THREE.Vector3) => THREE.Vector3} | null;
    if (!c) return null;
    if (c.target instanceof THREE.Vector3) return c.target;
    return typeof c.getTarget === "function" ? c.getTarget(new THREE.Vector3()) : null;
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
    const apply = () => {
        const on = orthographic();
        const t = target();
        updateProjection(camera, on, t ? camera.position.distanceTo(t) : camera.position.length());
    };
    // Whoever asks for the projection (a resize, a fit, the clip range) gets the right one.
    const own = camera.updateProjectionMatrix;
    camera.updateProjectionMatrix = apply;
    // And every frame, before the renderer reads it: a dolly moves the camera without asking.
    const before = scene.onBeforeRender;
    scene.onBeforeRender = function (this: THREE.Scene, ...args: Parameters<THREE.Scene["onBeforeRender"]>) {
        if (args[2] === camera && (orthographic() || camera.userData[ORTHO] === true)) apply();
        before.apply(this, args);
    };
    apply();
    return () => {
        camera.updateProjectionMatrix = own;
        scene.onBeforeRender = before;
        updateProjection(camera, false, 0);
    };
}
