/** Loading a spec-conformant (Y-up) glTF into this viewer's Z-up world.
 *
 * Two things have to hold, and the second is the one that bites:
 *
 *  1. The model stands up the RIGHT way. -90 degrees about X also stands it
 *     upright, mirrored front-to-back — indistinguishable in a screenshot of a
 *     roughly symmetric model, wrong in every coordinate.
 *
 *  2. The rotation composes with the loader's recentering frame WITHOUT
 *     rotating the offset. setupModelLoader derives one translation from the
 *     first model's bounding box and reuses it for every model loaded after,
 *     so models with very large base coordinates end up near the origin. If
 *     the rotation is applied after that box is measured, or underneath the
 *     offset in the transform chain, each model still looks correct ALONE and
 *     is silently displaced relative to the others.
 */
import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import {
    DEFAULT_SOURCE_UP_AXIS,
    applySourceUpAxis,
    sourceUpAxisMatrix,
    sourceUpAxisNeedsRotation,
    uprightSceneBox,
} from "../../utils/scene/sourceUpAxis";

const EPS = 1e-9;

function assertVec(actual: THREE.Vector3, expected: number[], msg: string) {
    assert.ok(
        Math.abs(actual.x - expected[0]) < EPS &&
            Math.abs(actual.y - expected[1]) < EPS &&
            Math.abs(actual.z - expected[2]) < EPS,
        msg + ": got (" + actual.x + ", " + actual.y + ", " + actual.z + "), want (" + expected.join(", ") + ")",
    );
}

/** A mesh at a known spot, wrapped in a root the way GLTFLoader hands one back. */
function modelAt(points: number[][]): {root: THREE.Object3D; mesh: THREE.Mesh} {
    const pos: number[] = [];
    for (const p of points) pos.push(...p);
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    const mesh = new THREE.Mesh(geom);
    const root = new THREE.Group();
    root.add(mesh);
    return {root, mesh};
}

/** What setupModelLoader does, in the order it does it: rotate, measure, then
 * either reuse a cached frame or derive one. Returns the world-space box and
 * the frame in force afterwards. */
function loadInto(
    root: THREE.Object3D,
    up: "z" | "y",
    cachedTranslation: THREE.Vector3 | null,
): {worldBox: THREE.Box3; translation: THREE.Vector3} {
    // The real call the loader makes: rotate + measure, bundled so the order
    // cannot be got wrong at the call site.
    const localBox = uprightSceneBox(root, up);
    let translation: THREE.Vector3;
    if (cachedTranslation) {
        translation = cachedTranslation;
    } else {
        translation = localBox.getCenter(new THREE.Vector3()).multiplyScalar(-1);
        const minZ = localBox.min.z;
        translation.z = -minZ + (localBox.max.z - minZ) * 0.05;
    }
    root.position.add(translation);
    root.updateWorldMatrix(false, true);
    return {worldBox: localBox.clone().translate(root.position), translation};
}

test("default is z-up: unchanged behaviour, no rotation applied", () => {
    assert.equal(DEFAULT_SOURCE_UP_AXIS, "z");
    assert.equal(sourceUpAxisNeedsRotation("z"), false);
    assert.equal(sourceUpAxisNeedsRotation(), false);
    assert.ok(sourceUpAxisMatrix("z").equals(new THREE.Matrix4()));

    const {root} = modelAt([[1, 2, 3]]);
    assert.equal(applySourceUpAxis(root, "z"), false);
    assert.equal(applySourceUpAxis(root), false);
    assert.ok(root.matrix.equals(new THREE.Matrix4()), "z-up root must stay identity");
});

test("y-up maps (x, y, z) -> (x, -z, y): +90 about X, not -90", () => {
    const m = sourceUpAxisMatrix("y");
    // The source's up axis must land on the scene's up axis.
    assertVec(new THREE.Vector3(0, 1, 0).applyMatrix4(m), [0, 0, 1], "source +Y -> scene +Z");
    // And the sign of the remaining axis pins the direction of the rotation:
    // -90 about X would send +Z to +Y and +Y to -Z.
    assertVec(new THREE.Vector3(0, 0, 1).applyMatrix4(m), [0, -1, 0], "source +Z -> scene -Y");
    assertVec(new THREE.Vector3(1, 0, 0).applyMatrix4(m), [1, 0, 0], "X is the rotation axis");
    // Right-handed and rigid: no mirroring, no scaling.
    assert.ok(Math.abs(m.determinant() - 1) < EPS, "must be a proper rotation (det +1)");
});

test("y-up rotation is exact: no floating-point dust from cos(PI/2)", () => {
    const m = sourceUpAxisMatrix("y");
    for (const v of m.elements) {
        assert.ok(v === 0 || v === 1 || v === -1, "matrix entry " + v + " is not exactly 0/1/-1");
    }
    // A large coordinate must survive the rotation bit-exactly.
    const big = new THREE.Vector3(20000, 10000, 1000).applyMatrix4(m);
    assert.deepEqual([big.x, big.y, big.z], [20000, -1000, 10000]);
});

test("a y-up model is measured AFTER rotation, so the frame is in scene axes", () => {
    // A tall thin column: 2 wide in the source's X and Z, 100 tall along its
    // up axis (+Y). Once upright it must be 100 tall along the scene's +Z.
    const {root} = modelAt([
        [-1, 0, -1],
        [1, 100, 1],
    ]);
    const {worldBox} = loadInto(root, "y", null);
    const size = worldBox.getSize(new THREE.Vector3());
    assertVec(size, [2, 2, 100], "upright column must be tall in scene Z");
    // Recentering puts the base just above the ground plane, never below it.
    assert.ok(worldBox.min.z > 0, "recentred model should sit on/above the grid");
    assert.ok(worldBox.min.z < 100, "and not be launched into orbit");
});

test("reusing one frame across a z-up and a y-up model preserves their displacement", () => {
    // The same physical point, expressed in each source's own convention. The
    // large base coordinates are the point: this is the case the shared frame
    // exists for.
    const zUpPoint = [20000, 15000, 1000];
    const yUpPoint = [zUpPoint[0], zUpPoint[2], -zUpPoint[1]]; // Z-up -> Y-up

    // Two models describing the SAME two physical points, each in its own
    // convention. The second point is offset from the first by `delta`.
    const delta = [7, -13, 4];
    const zUpPoint2 = [zUpPoint[0] + delta[0], zUpPoint[1] + delta[1], zUpPoint[2] + delta[2]];
    const yUpPoint2 = [zUpPoint2[0], zUpPoint2[2], -zUpPoint2[1]];
    const a = modelAt([zUpPoint, zUpPoint2]);
    const b = modelAt([yUpPoint, yUpPoint2]);

    // A loads first and establishes the frame; B reuses it (translate: true).
    const loadedA = loadInto(a.root, "z", null);
    const loadedB = loadInto(b.root, "y", loadedA.translation);

    // Both must have been recentred near the origin — the whole point of the
    // frame, given base coordinates in the tens of thousands.
    assert.ok(loadedA.worldBox.min.length() < 1000, "A should be recentred near the origin");
    assert.ok(loadedB.worldBox.min.length() < 1000, "B should be recentred near the origin");

    // The two sources describe the same geometry, so they must land on the same
    // box — not a rotated or displaced copy of it.
    assertVec(
        loadedB.worldBox.min,
        [loadedA.worldBox.min.x, loadedA.worldBox.min.y, loadedA.worldBox.min.z],
        "same geometry from two sources must land on the same box (min)",
    );
    assertVec(
        loadedB.worldBox.max,
        [loadedA.worldBox.max.x, loadedA.worldBox.max.y, loadedA.worldBox.max.z],
        "same geometry from two sources must land on the same box (max)",
    );

    // The invariant that matters: applying A's frame to B leaves corresponding
    // points coincident, so the A->B displacement is exactly what it was before
    // any recentering.
    const pA = new THREE.Vector3(zUpPoint[0], zUpPoint[1], zUpPoint[2]).applyMatrix4(a.root.matrixWorld);
    const pB = new THREE.Vector3(yUpPoint[0], yUpPoint[1], yUpPoint[2]).applyMatrix4(b.root.matrixWorld);
    assertVec(pB.clone().sub(pA), [0, 0, 0], "corresponding points must coincide");

    const pA2 = new THREE.Vector3(zUpPoint2[0], zUpPoint2[1], zUpPoint2[2]).applyMatrix4(a.root.matrixWorld);
    const pB2 = new THREE.Vector3(yUpPoint2[0], yUpPoint2[1], yUpPoint2[2]).applyMatrix4(b.root.matrixWorld);
    assertVec(pB2.clone().sub(pA2), [0, 0, 0], "and so must the second pair");
    // The displacement between the two points is preserved verbatim in scene axes.
    assertVec(pA2.clone().sub(pA), delta, "recentering must not alter relative geometry");
});

test("the recentering offset is translated, never rotated", () => {
    // Directly pins the transform order: three composes local = T * R * M, so
    // the offset added to `position` is a world-space translation. Compose it
    // the other way round (R * T) and the offset itself gets rotated — the
    // model looks upright but sits in the wrong place.
    const {root} = modelAt([[0, 0, 0]]);
    applySourceUpAxis(root, "y");
    const offset = new THREE.Vector3(3, 5, 7);
    root.position.add(offset);
    root.updateMatrixWorld(true);

    // The origin of the source maps to exactly the offset — unrotated.
    assertVec(
        new THREE.Vector3(0, 0, 0).applyMatrix4(root.matrixWorld),
        [3, 5, 7],
        "offset must survive the rotation untouched",
    );
    // And a source point is rotated and THEN shifted, not the reverse.
    assertVec(
        new THREE.Vector3(0, 1, 0).applyMatrix4(root.matrixWorld),
        [3, 5, 8],
        "source up axis must move along the scene up axis, from the offset",
    );
});

test("a root that carries its own transform is rotated along with its contents", () => {
    const {root} = modelAt([[0, 1, 0]]);
    root.position.set(0, 10, 0); // a glTF root may legitimately be placed
    applySourceUpAxis(root, "y");
    root.updateMatrixWorld(true);
    // (0,1,0) under the root's own +10 in Y is (0,11,0) in the source frame,
    // which is 11 up the scene's Z once converted.
    assertVec(
        new THREE.Vector3(0, 1, 0).applyMatrix4(root.matrixWorld),
        [0, 0, 11],
        "the root's own translation must be rotated too",
    );
});
