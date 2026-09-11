import assert from "node:assert/strict";
import test, {afterEach} from "node:test";

import * as THREE from "three";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";

import {
    clipWithModel,
    isClippedAway,
    setSectionClippingPlanes,
} from "@/utils/scene/section_clipping";
import {installResultLineSegments, pickResultLineSegment} from "@/utils/scene/fea/resultLineSegments";
import {installResultPointMarkers} from "@/utils/scene/fea/resultPointMarkers";
import {installUndeformedGhost} from "@/utils/scene/fea/undeformedGhost";

// Section planes clip per material, and the controller applies them in one walk of
// the scene when a plane changes. Overlays built after that walk -- the coloured
// beam lines a field repaint reinstalls, point markers, the undeformed outline --
// used to render unclipped until the next plane edit: colour a model by beam
// section with a plane on, and the beams it had cut away came back.
//
// The rule under test: an overlay built while a plane is enabled carries that
// plane from the moment it exists, as the same live instance the model holds.

afterEach(() => setSectionClippingPlanes(null));

/** Keeps x <= 0, cuts away x > 0. */
function cutPositiveX(): THREE.Plane[] {
    return [new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0)];
}

function feaMesh(): THREE.Mesh {
    const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    mesh.morphTargetInfluences = [0];
    return mesh;
}

/** Two vertical beams: E1 at x = -1 (kept by cutPositiveX), E2 at x = +1 (cut). */
function paintBeams(mesh: THREE.Mesh): LineSegments2 {
    installResultLineSegments(
        mesh,
        new Float32Array([-1, 0, 0, -1, 1, 0, 1, 0, 0, 1, 1, 0]),
        new Float32Array(12).fill(1),
        new Float32Array(12),
        ["E1", "E2"],
    );
    return mesh.getObjectByName("__fea_result_line_segments__") as LineSegments2;
}

function planesOf(object: THREE.Object3D): THREE.Plane[] | null {
    return ((object as THREE.Mesh).material as THREE.Material).clippingPlanes;
}

test("beams coloured after a plane was enabled are cut by that plane", () => {
    const planes = cutPositiveX();
    setSectionClippingPlanes(planes);
    const line = paintBeams(feaMesh());
    // The controller's own array, not a copy: a copy would stay where the plane was.
    assert.equal(planesOf(line), planes);
    assert.equal(line.userData.__clipWithModel, true, "untagged, the next plane edit would miss it");
});

test("a repaint rebuilds the beams, and the rebuilt ones are cut as well", () => {
    const planes = cutPositiveX();
    setSectionClippingPlanes(planes);
    const mesh = feaMesh();
    const first = paintBeams(mesh);
    const second = paintBeams(mesh);
    assert.notEqual(first, second, "a repaint is expected to build a new line");
    assert.equal(planesOf(second), planes);
});

test("the beams follow a gizmo drag, which moves the plane in place", () => {
    const planes = cutPositiveX();
    setSectionClippingPlanes(planes);
    const line = paintBeams(feaMesh());
    planes[0].constant = 5;
    assert.equal(planesOf(line)![0].constant, 5);
});

test("a beam's selection highlight is cut with it", () => {
    setSectionClippingPlanes(cutPositiveX());
    const mesh = feaMesh();
    paintBeams(mesh);
    const select = mesh.userData.__feaLineSelection as (ids: readonly string[]) => void;
    select(["E2"]);
    const highlight = mesh.getObjectByName("__fea_result_line_highlight__")!;
    assert.ok(highlight, "selecting E2 should draw a highlight");
    assert.ok(planesOf(highlight), "the highlight of a cut-away beam must not show through the cut");
});

test("result-point markers and the undeformed outline are cut too", () => {
    const planes = cutPositiveX();
    setSectionClippingPlanes(planes);
    const mesh = feaMesh();
    installResultPointMarkers(mesh, new Float32Array([1, 0, 0]), new Float32Array(3), new Float32Array(3));
    installUndeformedGhost(mesh, new Float32Array([0, 0, 0, 1, 0, 0]), new Uint32Array([0, 1]));
    assert.equal(planesOf(mesh.getObjectByName("__fea_result_point_markers__")!), planes);
    assert.equal(planesOf(mesh.getObjectByName("__fea_undeformed_ghost__")!), planes);
});

test("with no plane an overlay is only tagged, and its material left alone", () => {
    // Touching a material costs a shader recompile. The controller never does it
    // before a plane exists, and the seeding must not either.
    const overlay = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    clipWithModel(overlay);
    assert.equal(overlay.userData.__clipWithModel, true);
    assert.equal(planesOf(overlay), null);
    assert.equal((overlay.material as THREE.Material).version, 0);
});

test("switching every plane off stops seeding later overlays", () => {
    setSectionClippingPlanes(cutPositiveX());
    setSectionClippingPlanes([]);
    assert.equal(planesOf(paintBeams(feaMesh())), null);
});

test("everything under the root is seeded, not only the root", () => {
    // A glyph like an ArrowHelper is a group whose line and cone carry the materials.
    const planes = cutPositiveX();
    setSectionClippingPlanes(planes);
    const root = new THREE.Group();
    const child = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    root.add(child);
    clipWithModel(root);
    assert.equal(planesOf(child), planes);
    assert.equal(child.userData.__clipWithModel, true);
});

/** Pick straight down -z at (x, 0.5), the way a click on that beam would. */
function pickAt(mesh: THREE.Mesh, x: number): string | null {
    const line = mesh.getObjectByName("__fea_result_line_segments__") as LineSegments2;
    // What the per-frame hook sets from the renderer; with the default 1x1 a three-pixel
    // line is wider than the screen and every beam is hit.
    (line.material as LineMaterial).resolution.set(800, 600);
    const camera = new THREE.PerspectiveCamera(50, 800 / 600, 0.1, 100);
    camera.position.set(x, 0.5, 10);
    camera.lookAt(x, 0.5, 0);
    camera.updateMatrixWorld();
    mesh.updateMatrixWorld(true);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(0, 0), camera);
    return pickResultLineSegment(mesh, raycaster)?.rangeId ?? null;
}

test("a beam the plane cut away cannot be clicked through the cut", () => {
    const open = feaMesh();
    paintBeams(open);
    assert.equal(pickAt(open, 1), "E2", "without a plane E2 is where the click lands");

    setSectionClippingPlanes(cutPositiveX());
    const cut = feaMesh();
    paintBeams(cut);
    assert.equal(pickAt(cut, 1), null);
    assert.equal(pickAt(cut, -1), "E1", "the kept beam still picks");
});

test("a point is cut away by any one plane, and kept on the plane itself", () => {
    const planes = [...cutPositiveX(), new THREE.Plane(new THREE.Vector3(0, -1, 0), 0)];
    assert.equal(isClippedAway(new THREE.Vector3(-1, -1, 0), planes), false);
    assert.equal(isClippedAway(new THREE.Vector3(0, 0, 0), planes), false);
    assert.equal(isClippedAway(new THREE.Vector3(1, -1, 0), planes), true);
    assert.equal(isClippedAway(new THREE.Vector3(-1, 1, 0), planes), true);
    assert.equal(isClippedAway(new THREE.Vector3(1, 1, 0), null), false);
});
