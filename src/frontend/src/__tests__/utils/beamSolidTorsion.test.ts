import assert from "node:assert/strict";
import test from "node:test";

import type {ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import type {FeaManifestField} from "@/services/viewerApi";
import {beamSolidDisplacement} from "@/utils/scene/fea/streaming/warp";
import {rotationOffsets} from "@/utils/scene/fea/warpComponents";

// A beam solid's vertices are driven by the two end nodes of its beam. The warp
// used to take only their translations, so a torsion mode -- whose beam axis does
// not translate at all -- left every solid standing still. The rotation term
// turns each section about its axis: delta = lerp(u) + lerp(θ) × r.

/** One beam along X from node 0 (0,0,0) to node 1 (2,0,0); one solid vertex at
 *  mid-span, offset 0.1 in +Y from the axis. */
const nodePositions = new Float32Array([0, 0, 0, 2, 0, 0]);
const vertexBase = new Float32Array([1, 0.1, 0]);
const warp: ParsedBeamSolidsWarp = {
    n_verts: 1,
    node0: new Uint32Array([0]),
    node1: new Uint32Array([1]),
    t: new Float32Array([0.5]),
} as ParsedBeamSolidsWarp;

function field(components: string[]): FeaManifestField {
    return {name_canonical: "U", components} as unknown as FeaManifestField;
}

test("a twist about the beam axis turns the section", () => {
    // Node 0 fixed, node 1 twisted 0.2 rad about X: mid-span twist 0.1 rad.
    const f = field(["X", "Y", "Z", "RX", "RY", "RZ"]);
    const step = new Float32Array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0.2, 0, 0]);
    const d = beamSolidDisplacement(warp, vertexBase, f, step, nodePositions);
    // θ × r = (0.1, 0, 0) × (0, 0.1, 0) = (0, 0, 0.01): the +Y offset swings toward +Z.
    assert.deepEqual(Array.from(d).map((v) => Math.round(v * 1e6) / 1e6), [0, 0, 0.01]);
});

test("translation still applies, and without node positions it is all there is", () => {
    const f = field(["X", "Y", "Z", "RX", "RY", "RZ"]);
    const step = new Float32Array([0, 1, 0, 0, 0, 0, 0, 3, 0, 0.2, 0, 0]);
    const withRot = beamSolidDisplacement(warp, vertexBase, f, step, nodePositions);
    assert.ok(Math.abs(withRot[1] - 2) < 1e-6); // lerp of 1 and 3
    assert.ok(Math.abs(withRot[2] - 0.01) < 1e-6);
    const translationOnly = beamSolidDisplacement(warp, vertexBase, f, step);
    assert.deepEqual(Array.from(translationOnly), [0, 2, 0]);
});

test("Sesam's leading ALL reduction is neither a translation nor a rotation", () => {
    const f = field(["ALL", "X", "Y", "Z", "RX", "RY", "RZ"]);
    const step = new Float32Array([9, 0, 0, 0, 0, 0, 0, 9, 0, 0, 0, 0.2, 0, 0]);
    const d = beamSolidDisplacement(warp, vertexBase, f, step, nodePositions);
    assert.deepEqual(Array.from(d).map((v) => Math.round(v * 1e6) / 1e6), [0, 0, 0.01]);
});

test("rotation slots are found by name across solvers, never by position", () => {
    assert.deepEqual(rotationOffsets(field(["X", "Y", "Z", "RX", "RY", "RZ"])), [3, 4, 5]);
    assert.deepEqual(rotationOffsets(field(["DX", "DY", "DZ", "DRX", "DRY", "DRZ"])), [3, 4, 5]);
    assert.deepEqual(rotationOffsets(field(["U1", "U2", "U3", "UR1", "UR2", "UR3"])), [3, 4, 5]);
    assert.equal(rotationOffsets(field(["X", "Y", "Z"])), null);
    assert.equal(rotationOffsets(field(["a", "b", "c", "d", "e", "f"])), null);
});
