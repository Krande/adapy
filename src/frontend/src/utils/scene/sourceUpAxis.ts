import * as THREE from "three";

import {fastSceneBox} from "./boundsFast";

// The viewer's world is Z-up: ``ThreeCanvas`` sets
// ``THREE.Object3D.DEFAULT_UP = (0, 0, 1)`` and every camera / grid / gizmo
// follows from that. Geometry adapy exports carries Z-up coordinates directly,
// so it needs no conversion — that is the ``"z"`` case and the default.
//
// glTF 2.0, however, specifies a **Y-up** coordinate system, so a
// spec-conformant glTF from any other producer arrives rotated 90 degrees
// relative to a Z-up scene: it lies on its side. Such a file usually bakes the
// convention into its vertex data rather than into a root node transform, so
// there is nothing in the asset to detect it by — the caller has to say which
// convention the bytes follow.
//
// This module is the single place that knows the conversion. It is deliberately
// pure (an axis label in, a transform out) so the ordering rules below can be
// unit-tested without a scene, a loader or a browser.

/** Which axis points up in the source asset's own coordinates. */
export type SourceUpAxis = "z" | "y";

/**
 * Default source convention: ``"z"``, i.e. the content already matches the
 * viewer's Z-up world and no rotation is applied. Every pre-existing caller
 * keeps this behaviour.
 */
export const DEFAULT_SOURCE_UP_AXIS: SourceUpAxis = "z";

/**
 * The rotation that carries geometry authored with `up` as its up axis into the
 * viewer's Z-up world.
 *
 * For ``"y"`` this is +90 degrees about **X**, mapping (x, y, z) -> (x, -z, y):
 * the source's up axis (+Y) lands on +Z, and the source's +Z lands on -Y. That
 * is the exact inverse of the Z-up -> Y-up rotation a glTF exporter applies on
 * the way out, so a round trip through a spec-conformant glTF is the identity.
 *
 * The sign matters and is easy to get wrong: the opposite rotation (-90 degrees
 * about X) also stands the model upright, but mirrored front-to-back through the
 * horizontal plane — plausible in a screenshot, wrong in the coordinates.
 */
export function sourceUpAxisMatrix(up: SourceUpAxis = DEFAULT_SOURCE_UP_AXIS): THREE.Matrix4 {
    if (up === "y") {
        // Written out literally rather than via makeRotationX(PI/2) so the
        // entries are exact zeros and ones. cos(PI/2) is 6.1e-17 in floating
        // point, and models here can sit at coordinates in the tens of
        // thousands, where that leaks into the low bits of every vertex.
        return new THREE.Matrix4().set(
            1, 0, 0, 0,
            0, 0, -1, 0,
            0, 1, 0, 0,
            0, 0, 0, 1,
        );
    }
    return new THREE.Matrix4();
}

/** True when `up` needs any conversion at all (i.e. it is not already Z-up). */
export function sourceUpAxisNeedsRotation(up: SourceUpAxis = DEFAULT_SOURCE_UP_AXIS): boolean {
    return up === "y";
}

/**
 * Rotate a freshly loaded model root into the viewer's Z-up world, in place.
 *
 * **This must run before the model's bounding box is measured.** The loader
 * derives the scene-wide recentering offset from that box, and every model
 * loaded afterwards reuses the same offset so overlays land in one shared
 * frame. Measure first and rotate second and the offset is computed in the
 * source's own (Y-up) axes, then applied in the scene's (Z-up) axes — the model
 * still looks upright on its own but sits somewhere else entirely, which only
 * shows up once a second model is in the scene to compare it against.
 *
 * The rotation is *pre*-multiplied onto the root's own transform, so the root's
 * local matrix ends up ``R * M``. The recentering offset is added to the root's
 * ``position`` afterwards, which three.js composes as ``T * R * M``: the offset
 * therefore stays a world-space translation and is **not** itself rotated. That
 * is the property that keeps two models in one shared frame — see
 * ``sourceUpAxis.test.ts``, which pins it.
 *
 * Rotating the root (rather than baking the rotation into each geometry) also
 * leaves every mesh's *local* matrix untouched, so the edge overlays that
 * ``prepareLoadedModel`` bakes from local matrices stay aligned with their
 * meshes, and picking / bounding boxes / view-centering all keep going through
 * ``matrixWorld`` as before.
 *
 * @returns whether a rotation was actually applied.
 */
export function applySourceUpAxis(
    root: THREE.Object3D,
    up: SourceUpAxis = DEFAULT_SOURCE_UP_AXIS,
): boolean {
    if (!sourceUpAxisNeedsRotation(up)) return false;

    // Compose -> premultiply -> decompose rather than just touching
    // ``root.quaternion``: a glTF root may legitimately carry its own
    // translation/scale, and those have to be rotated along with everything
    // else. In practice GLTFLoader hands back an identity root and this reduces
    // to setting the quaternion.
    const local = new THREE.Matrix4().compose(root.position, root.quaternion, root.scale);
    local.premultiply(sourceUpAxisMatrix(up));
    local.decompose(root.position, root.quaternion, root.scale);
    root.updateMatrix();
    return true;
}

/**
 * Stand a freshly loaded model up and measure it, in that order.
 *
 * The two steps are bundled deliberately. The loader needs the model's bounding
 * box to derive the scene-wide recentering frame, and that box is only in scene
 * axes if the model has already been rotated — but nothing about a call to
 * "measure the box" makes that dependency visible, and getting it backwards
 * fails silently (a model that looks right alone and sits in the wrong place
 * next to another). Exposing only the combined operation means a caller cannot
 * express the broken order.
 *
 * @returns the root's bounding box in scene axes, before recentering.
 */
export function uprightSceneBox(
    root: THREE.Object3D,
    up: SourceUpAxis = DEFAULT_SOURCE_UP_AXIS,
): THREE.Box3 {
    applySourceUpAxis(root, up);
    // fastSceneBox transforms each geometry box by its mesh's matrixWorld, and
    // refreshes those world matrices itself — so the rotation just applied to
    // the root is already folded in.
    return fastSceneBox(root);
}
