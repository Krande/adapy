import assert from "node:assert/strict";
import test from "node:test";

import type {ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import type {FeaManifestField} from "@/services/viewerApi";
import {beamSolidColors} from "@/utils/scene/fea/embedBeamSolids";
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

test("a twisting solid is coloured by how far it moves, root to tip", () => {
    // Three vertices moving 0, 0.5 and 1 (a twist growing toward the tip): blue -> green -> red.
    const colors = beamSolidColors(new Float32Array([0, 0, 0, 0, 0, 0.5, 0, 0, 1]));
    assert.deepEqual(Array.from(colors.slice(0, 3)), [0, 0, 1]);
    assert.deepEqual(Array.from(colors.slice(3, 6)), [0, 1, 0]);
    assert.deepEqual(Array.from(colors.slice(6, 9)), [1, 0, 0]);
});

test("rotation slots are found by name across solvers, never by position", () => {
    assert.deepEqual(rotationOffsets(field(["X", "Y", "Z", "RX", "RY", "RZ"])), [3, 4, 5]);
    assert.deepEqual(rotationOffsets(field(["DX", "DY", "DZ", "DRX", "DRY", "DRZ"])), [3, 4, 5]);
    assert.deepEqual(rotationOffsets(field(["U1", "U2", "U3", "UR1", "UR2", "UR3"])), [3, 4, 5]);
    assert.equal(rotationOffsets(field(["X", "Y", "Z"])), null);
    assert.equal(rotationOffsets(field(["a", "b", "c", "d", "e", "f"])), null);
});

// A field without rotations (Abaqus `U` is U1..U3; UR is a separate output): the
// section turn comes from the beams' own axes. Before, the sections only
// translated, so a boom swinging 45° about X kept its end planes where they began.

/** A square of beams in the XY plane: 0-1 along Y, 0-2 and 1-3 along X (the
 *  bracing a lattice boom has), 2-3 along Y. One solid vertex at mid-span of 0-1
 *  and one of 0-2, each 0.1 off its axis in +Z. */
const squareNodes = new Float32Array([0, 0, 0, 0, 2, 0, 1, 0, 0, 1, 2, 0]);
const squareVerts = new Float32Array([0, 1, 0.1, 0.5, 0, 0.1]);
function squareWarp(): ParsedBeamSolidsWarp {
    // Every beam is listed (one vertex each past the two probed ones) so the
    // graph sees the whole square.
    return {
        n_verts: 4,
        node0: new Uint32Array([0, 0, 2, 1]),
        node1: new Uint32Array([1, 2, 3, 3]),
        t: new Float32Array([0.5, 0.5, 0.5, 0.5]),
    } as ParsedBeamSolidsWarp;
}

function rotX(a: number, p: ArrayLike<number>, i: number): [number, number, number] {
    const c = Math.cos(a), s = Math.sin(a);
    return [p[i], c * p[i + 1] - s * p[i + 2], s * p[i + 1] + c * p[i + 2]];
}

test("a rigid 45° swing turns every section with it, the bracing along the axis too", () => {
    const a = Math.PI / 4;
    const step = new Float32Array(12);
    for (let n = 0; n < 4; n++) {
        const q = rotX(a, squareNodes, n * 3);
        for (let k = 0; k < 3; k++) step[n * 3 + k] = q[k] - squareNodes[n * 3 + k];
    }
    const f = {name_canonical: "U", components: ["U1", "U2", "U3"], analysis_kind: "transient"} as unknown as FeaManifestField;
    const verts = new Float32Array([...squareVerts, 1, 1, 0.1, 0.5, 2, 0.1]);
    const d = beamSolidDisplacement(squareWarp(), verts, f, step, squareNodes);
    for (let v = 0; v < 2; v++) {
        const want = rotX(a, verts, v * 3);
        for (let k = 0; k < 3; k++) {
            assert.ok(Math.abs(verts[v * 3 + k] + d[v * 3 + k] - want[k]) < 1e-5, `vertex ${v} axis ${k}`);
        }
    }
});

test("a lone beam takes the turn of its own axis", () => {
    const w = {n_verts: 1, node0: new Uint32Array([0]), node1: new Uint32Array([1]), t: new Float32Array([0.5])} as ParsedBeamSolidsWarp;
    const nodes = new Float32Array([0, 0, 0, 0, 2, 0]);
    const a = 0.7;
    const q = rotX(a, nodes, 3);
    const step = new Float32Array([0, 0, 0, q[0] - 0, q[1] - 2, q[2] - 0]);
    const f = {name_canonical: "U", components: ["U1", "U2", "U3"], analysis_kind: "transient"} as unknown as FeaManifestField;
    const base = new Float32Array([0, 1, 0.1]);
    const d = beamSolidDisplacement(w, base, f, step, nodes);
    const want = rotX(a, base, 0);
    for (let k = 0; k < 3; k++) assert.ok(Math.abs(base[k] + d[k] - want[k]) < 1e-5);
});

test("a mode shape without rotations gets the small-rotation turn, linear in the amplitude", () => {
    const w = {n_verts: 1, node0: new Uint32Array([0]), node1: new Uint32Array([1]), t: new Float32Array([0.5])} as ParsedBeamSolidsWarp;
    const nodes = new Float32Array([0, 0, 0, 0, 2, 0]);
    const f = {name_canonical: "U", components: ["U1", "U2", "U3"], analysis_kind: "eigen"} as unknown as FeaManifestField;
    const base = new Float32Array([0, 1, 0.1]);
    const d1 = beamSolidDisplacement(w, base, f, new Float32Array([0, 0, 0, 0, 0, 0.2]), nodes);
    const d2 = beamSolidDisplacement(w, base, f, new Float32Array([0, 0, 0, 0, 0, 0.4]), nodes);
    // Tip lifted 0.2 over a 2 long beam: θ = 0.1 about X; mid-span lift 0.1, and
    // the +Z offset swings by θ × r = (0.1,0,0) × (0,0,0.1) = (0, -0.01, 0).
    assert.ok(Math.abs(d1[1] + 0.01) < 1e-6 && Math.abs(d1[2] - 0.1) < 1e-6, String(Array.from(d1)));
    for (let k = 0; k < 3; k++) assert.ok(Math.abs(d2[k] - 2 * d1[k]) < 1e-6);
});
