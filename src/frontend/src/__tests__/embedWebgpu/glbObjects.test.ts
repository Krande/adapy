// The WebGPU embed's object naming: replaying adacpp's GLB batching on the JSON alone names each
// segment a pick can report, and a replay that disagrees with what was loaded names nothing.
import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {glbJson, matchGeometries, objectPath, replayBatches, segmentsFromRanges} from "../../../embed-webgpu/glbObjects";

test("draw ranges become segments in index order, gaps included", () => {
    const segs = segmentsFromRanges({b: [10, 5], a: [0, 6]}, 20);
    assert.deepEqual(segs, [
        [0, 6, {id: "a"}],
        [6, 4, null],
        [10, 5, {id: "b"}],
        [15, 5, null],
    ]);
    assert.deepEqual(segmentsFromRanges(null, 9), []);
});

test("primitives batch by topology and material, in the reader's order", () => {
    const json = {
        accessors: [{count: 4}, {count: 6}, {count: 3}, {count: 2}],
        meshes: [
            {primitives: [{attributes: {POSITION: 0}, indices: 1, material: 1}]},
            {primitives: [{attributes: {POSITION: 2}, material: 0}]},
            {primitives: [{attributes: {POSITION: 3}, mode: 1}]},
        ],
        nodes: [{name: "plate", mesh: 0}, {name: "beam", mesh: 1}, {name: "edges", mesh: 2}],
        scenes: [{nodes: [0, 1, 2], extras: {draw_ranges_plate: {"7": [0, 6]}}}],
    };
    const recs = replayBatches(json);
    // triangles/material 0 (beam), triangles/material 1 (plate), then lines
    assert.deepEqual(recs.map((r) => [r.topology, r.vertexCount]), [[0, 3], [0, 4], [1, 2]]);
    assert.deepEqual(recs[0].segments, [{node: "beam"}]);
    assert.deepEqual(recs[1].segments, [{id: "7"}]);
});

test("a geometry the replay disagrees with gets no names", () => {
    const recs = [{topology: 0, vertexCount: 3, segments: [{id: "a"}]}, {topology: 0, vertexCount: 5, segments: [null]}];
    const loaded = [
        {geometry: 1, instance: 11, topology: 0, vertexCount: 3, featureEdges: false},
        {geometry: 2, instance: 12, topology: 1, vertexCount: 9, featureEdges: true},
        {geometry: 3, instance: 13, topology: 0, vertexCount: 5, featureEdges: false},
    ];
    const m = matchGeometries(recs, loaded, (g) => (g === 3 ? 2 : 1));
    assert.deepEqual(m.get(11), [{id: "a"}]);
    assert.equal(m.has(13), false); // the reader made 2 segments, the replay 1
});

test("an adapy GLB names its object and its parents", () => {
    const glb = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../../verification/_assets/beam.glb");
    if (!fs.existsSync(glb)) return; // the verification assets are generated
    const json = glbJson(new Uint8Array(fs.readFileSync(glb)));
    assert.ok(json);
    const recs = replayBatches(json);
    assert.equal(recs.length, 1);
    assert.deepEqual(recs[0].segments, [{id: "2"}]);
    assert.deepEqual(objectPath(json, "2"), ["verification_beam", "beam_only", "MyBeam"]);
});
