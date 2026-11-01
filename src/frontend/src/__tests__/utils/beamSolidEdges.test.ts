import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

import {
    BEAM_SOLID_EDGES_NAME,
    beamSolidFeatureEdges,
    ensureBeamSolidEdges,
    maskBeamSolidEdges,
    warpBeamSolidEdges,
    warpBeamSolidEdgesFromRender,
    type BeamSolidEdgeOwners,
} from "@/utils/scene/fea/beamSolidEdges";

/** A closed box (unit square section, length 2 along Y) offset along X by `dx`,
 *  split per face as a flat-shaded solid is: 6 faces × 4 vertices, 12 triangles. */
function box(dx: number, base: number): {pos: number[]; idx: number[]} {
    const c = (x: number, y: number, z: number) => [x + dx, y, z];
    const faces = [
        [c(0, 0, 0), c(1, 0, 0), c(1, 0, 1), c(0, 0, 1)], // end cap y=0
        [c(0, 2, 0), c(0, 2, 1), c(1, 2, 1), c(1, 2, 0)], // end cap y=2
        [c(0, 0, 0), c(0, 0, 1), c(0, 2, 1), c(0, 2, 0)],
        [c(1, 0, 0), c(1, 2, 0), c(1, 2, 1), c(1, 0, 1)],
        [c(0, 0, 0), c(0, 2, 0), c(1, 2, 0), c(1, 0, 0)],
        [c(0, 0, 1), c(1, 0, 1), c(1, 2, 1), c(0, 2, 1)],
    ];
    const pos: number[] = [];
    const idx: number[] = [];
    faces.forEach((f, i) => {
        f.forEach((p) => pos.push(...p));
        const o = base + i * 4;
        idx.push(o, o + 1, o + 2, o, o + 2, o + 3);
    });
    return {pos, idx};
}

test("a box section's edges are its 12 corners and outlines, not its face diagonals", () => {
    const {pos, idx} = box(0, 0);
    const pairs = beamSolidFeatureEdges(new Float32Array(pos), idx, [[0, idx.length]]);
    assert.equal(pairs.length / 2, 12);
});

test("each element keeps its own outline where two meet", () => {
    // Two boxes end to end along Y would share the cap at y=2; per element both
    // caps stay. Here: two separate ranges of the same box shape.
    const a = box(0, 0);
    const b = box(0, 24);
    const pos = new Float32Array([...a.pos, ...b.pos]);
    const idx = [...a.idx, ...b.idx];
    const owners: number[] = [];
    const pairs = beamSolidFeatureEdges(pos, idx, [[0, 36], [36, 36]], 31, owners);
    assert.equal(pairs.length / 2, 24);
    assert.deepEqual([...new Set(owners)], [0, 1]);
});

test("a finely faceted round section is smooth along its length", () => {
    // A 16-sided prism: the 22.5° folds are not edges; the two caps' rims are.
    const n = 16;
    const pos: number[] = [];
    const idx: number[] = [];
    for (let i = 0; i < n; i++) {
        const a0 = (i / n) * 2 * Math.PI, a1 = ((i + 1) / n) * 2 * Math.PI;
        const o = pos.length / 3;
        pos.push(Math.cos(a0), 0, Math.sin(a0), Math.cos(a1), 0, Math.sin(a1), Math.cos(a1), 2, Math.sin(a1), Math.cos(a0), 2, Math.sin(a0));
        idx.push(o, o + 1, o + 2, o, o + 2, o + 3);
    }
    const pairs = beamSolidFeatureEdges(new Float32Array(pos), idx, [[0, idx.length]]);
    // Open prism (no caps): boundary rims only, 16 + 16.
    assert.equal(pairs.length / 2, 32);
});

test("hidden elements' edges collapse to a point and come back", () => {
    const owners: BeamSolidEdgeOwners = {
        all: new Uint32Array([0, 1, 2, 3, 4, 5]),
        rangeIds: ["E1", "E2"],
        owner: new Uint32Array([0, 1, 1]),
    };
    const out = new Uint32Array(6);
    maskBeamSolidEdges(owners, (id) => id === "E2", out);
    assert.deepEqual(Array.from(out), [0, 1, 2, 2, 4, 4]);
    maskBeamSolidEdges(owners, () => false, out);
    assert.deepEqual(Array.from(out), [0, 1, 2, 3, 4, 5]);
});

test("the overlay is built once, in source numbering, and follows the solid's warp", () => {
    const {pos, idx} = box(0, 0);
    const base = new Float32Array(pos);
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(Float32Array.from(base), 3));
    geom.setIndex(idx);
    const solids = new THREE.Mesh(geom, new THREE.MeshBasicMaterial()) as unknown as THREE.Mesh & {drawRanges: Map<string, [number, number]>};
    solids.drawRanges = new Map([["E7", [0, 36]]]);
    solids.morphTargetInfluences = [2];

    const disp = new Float32Array(base.length).fill(0.5);
    warpBeamSolidEdges(solids, disp); // before the overlay exists: remembered
    const overlay = ensureBeamSolidEdges(solids, base)!;
    assert.ok(overlay);
    assert.equal(overlay.name, BEAM_SOLID_EDGES_NAME);
    assert.equal(ensureBeamSolidEdges(solids, base), overlay, "once");
    const mat = overlay.material as THREE.LineBasicMaterial;
    assert.ok(mat.transparent);
    assert.notEqual(mat.onBeforeRender, THREE.Material.prototype.onBeforeRender, "fades with density like the shell grid");
    const morph = () => Array.from((overlay.geometry.morphAttributes.position[0] as THREE.BufferAttribute).array as Float32Array);
    assert.ok(morph().every((v) => v === 0.5));

    warpBeamSolidEdges(solids, new Float32Array(base.length).fill(1));
    assert.ok(morph().every((v) => v === 1));

    // A recorded frame keeps the solid's (here unexpanded) render morph.
    warpBeamSolidEdgesFromRender(solids, new Float32Array(base.length).fill(3));
    assert.ok(morph().every((v) => v === 3));
});
