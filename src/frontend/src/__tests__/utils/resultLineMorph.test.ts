import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";

import {
    hasResultLineSegments,
    installResultLineSegments,
    setResultLineSegmentsVisible,
} from "@/utils/scene/fea/resultLineSegments";

// The beam lines are deformed on the CPU, and the trap that makes that wrong is
// invisible in the code that does it: LineSegmentsGeometry.setPositions KEEPS the
// Float32Array it is handed instead of copying it, so a driver that reads its
// undeformed positions out of the geometry and writes the deformed ones back is
// reading its own last answer. On a slider drag or an oscillation the beams walk
// away from the model a little further every frame, while the shells — morphed on
// the GPU from an untouched base — stay put.
//
// This exercises the driver the way a render does: set an influence, run the
// hook, read the positions back.

/** One two-node beam from (0,0,0) to (1,0,0), displaced +1 in Y at the far end. */
function oneBeam() {
    const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    mesh.morphTargetInfluences = [0];
    const positions = new Float32Array([0, 0, 0, 1, 0, 0]);
    const colors = new Float32Array([1, 0, 0, 1, 0, 0]);
    const displacement = new Float32Array([0, 0, 0, 0, 1, 0]);
    installResultLineSegments(mesh, positions, colors, displacement, ["E1"]);
    const line = mesh.getObjectByName("__fea_result_line_segments__") as LineSegments2;
    return {mesh, line};
}

/** Run the per-frame hook the renderer would call.
 *
 * Called through an unknown-arity cast because ``onBeforeRender``'s signature has
 * changed across the three.js versions this repo and its plugin overlay resolve;
 * the hook itself only reads the renderer. */
function draw(line: LineSegments2): void {
    const renderer = {
        getSize: (target: THREE.Vector2) => target.set(800, 600),
    } as unknown as THREE.WebGLRenderer;
    const hook = line.onBeforeRender as unknown as (...args: unknown[]) => void;
    hook.call(line, renderer, null, null, null, null, null);
}

function positionsOf(line: LineSegments2): number[] {
    const attr = line.geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute;
    return Array.from(attr.data.array as Float32Array);
}

test("an influence applied twice gives the same shape, not twice the shape", () => {
    const {mesh, line} = oneBeam();

    mesh.morphTargetInfluences![0] = 1;
    draw(line);
    const first = positionsOf(line);

    // A different influence and back again — what a slider drag does.
    mesh.morphTargetInfluences![0] = 0.5;
    draw(line);
    mesh.morphTargetInfluences![0] = 1;
    draw(line);

    assert.deepEqual(positionsOf(line), first);
});

test("the far end moves by exactly the influence, at any point in a sweep", () => {
    const {mesh, line} = oneBeam();
    // A full oscillation, the way the playback driver walks the influence.
    for (const influence of [0.2, 0.6, 1, 0.6, 0.2, 0]) {
        mesh.morphTargetInfluences![0] = influence;
        draw(line);
        const pos = positionsOf(line);
        // Near end pinned; far end lifted by the influence and nothing more.
        assert.deepEqual(pos.slice(0, 3), [0, 0, 0]);
        assert.equal(pos[3], 1);
        assert.ok(
            Math.abs(pos[4] - influence) < 1e-6,
            `influence ${influence} moved the end to ${pos[4]}`,
        );
    }
});

test("returning to zero returns the beam to where the model says it is", () => {
    const {mesh, line} = oneBeam();
    for (const influence of [1, 0.3, 0.9, 0]) {
        mesh.morphTargetInfluences![0] = influence;
        draw(line);
    }
    assert.deepEqual(positionsOf(line), [0, 0, 0, 1, 0, 0]);
});

test("the segment ids ride along, so a hit can be named", () => {
    const {line} = oneBeam();
    assert.deepEqual(line.userData.__feaSegmentIds, ["E1"]);
});

test("visibility is a switch on what was built, not a rebuild", () => {
    const {mesh, line} = oneBeam();
    setResultLineSegmentsVisible(mesh, false);
    assert.equal(line.visible, false);
    setResultLineSegmentsVisible(mesh, true);
    assert.equal(line.visible, true);
});

test("a mesh with no beam lines is honest about it", () => {
    // Only an element field installs them. A nodal field clears them and paints the
    // shells, so "result colours are on" does not mean "a beam is being drawn in
    // colour" — and the grey element edge must not stand aside for a line that is
    // not there.
    const bare = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    assert.equal(hasResultLineSegments(bare), false);
    const {mesh} = oneBeam();
    assert.equal(hasResultLineSegments(mesh), true);
});
