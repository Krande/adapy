// A GLB's first mesh as typed arrays, read without a scene library: the vertices in file order (the
// numbering an FEA result's fields are stored in), the indices widened to 32 bits, and where the
// mesh's node stands.
import assert from "node:assert/strict";
import {test} from "node:test";

import {readGlbMesh} from "@/utils/glb/glbMesh";

/** A GLB from a glTF document and its binary chunk. */
function glb(json: unknown, bin: Uint8Array): Uint8Array {
    const pad = (bytes: Uint8Array, fill: number) => {
        const out = new Uint8Array(Math.ceil(bytes.length / 4) * 4).fill(fill);
        out.set(bytes);
        return out;
    };
    const jsonChunk = pad(new TextEncoder().encode(JSON.stringify(json)), 0x20);
    const binChunk = pad(bin, 0);
    const out = new Uint8Array(12 + 8 + jsonChunk.length + 8 + binChunk.length);
    const view = new DataView(out.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, out.length, true);
    view.setUint32(12, jsonChunk.length, true);
    view.setUint32(16, 0x4e4f534a, true);
    out.set(jsonChunk, 20);
    view.setUint32(20 + jsonChunk.length, binChunk.length, true);
    view.setUint32(24 + jsonChunk.length, 0x004e4942, true);
    out.set(binChunk, 28 + jsonChunk.length);
    return out;
}

const POSITIONS = [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0];
const INDICES = [0, 1, 2, 0, 2, 3];

/** Four vertices and two triangles; indices as 16-bit, positions packed or with a stride. */
function quad(opts: {stride?: number; node?: Record<string, unknown>; mode?: number; indexed?: boolean} = {}): Uint8Array {
    const stride = opts.stride ?? 12;
    const indexed = opts.indexed ?? true;
    const bin = new Uint8Array(12 + 4 * stride);
    const view = new DataView(bin.buffer);
    INDICES.forEach((v, i) => view.setUint16(i * 2, v, true));
    for (let v = 0; v < 4; v++) for (let c = 0; c < 3; c++) view.setFloat32(12 + v * stride + c * 4, POSITIONS[v * 3 + c], true);
    return glb(
        {
            asset: {version: "2.0"},
            scene: 0,
            scenes: [{nodes: [0]}],
            nodes: [{name: "world", children: [1]}, {name: "mesh", mesh: 0, ...(opts.node ?? {})}],
            meshes: [{primitives: [{attributes: {POSITION: 1}, ...(indexed ? {indices: 0} : {}), ...(opts.mode !== undefined ? {mode: opts.mode} : {})}]}],
            accessors: [
                {bufferView: 0, componentType: 5123, count: 6, type: "SCALAR"},
                {bufferView: 1, componentType: 5126, count: 4, type: "VEC3"},
            ],
            bufferViews: [
                {buffer: 0, byteOffset: 0, byteLength: 12},
                {buffer: 0, byteOffset: 12, byteLength: 4 * stride, ...(stride !== 12 ? {byteStride: stride} : {})},
            ],
            buffers: [{byteLength: bin.length}],
        },
        bin,
    );
}

test("positions come out in file order and indices as 32-bit", () => {
    const mesh = readGlbMesh(quad());
    assert.deepEqual([...mesh.positions], POSITIONS);
    assert.ok(mesh.indices instanceof Uint32Array);
    assert.deepEqual([...mesh.indices!], INDICES);
    assert.equal(mesh.topology, "triangles");
    assert.deepEqual([...mesh.matrix], [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
});

test("interleaved positions are read through their stride", () => {
    assert.deepEqual([...readGlbMesh(quad({stride: 20})).positions], POSITIONS);
});

test("the mesh's node says where it stands: a translation, or a matrix under its parent", () => {
    const moved = readGlbMesh(quad({node: {translation: [5, 6, 7], scale: [2, 2, 2]}}));
    assert.deepEqual([...moved.matrix], [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 5, 6, 7, 1]);
    // A quarter turn about x, as a y-up export carries: +y becomes +z.
    const h = Math.SQRT1_2;
    const turned = readGlbMesh(quad({node: {rotation: [h, 0, 0, h]}}));
    const expected = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1];
    turned.matrix.forEach((v, i) => assert.ok(Math.abs(v - expected[i]) < 1e-6, `matrix[${i}] = ${v}`));
});

test("a point cloud (the mesh of a model with no faces) has no indices", () => {
    const mesh = readGlbMesh(quad({mode: 0, indexed: false}));
    assert.equal(mesh.topology, "points");
    assert.equal(mesh.indices, null);
    assert.equal(mesh.positions.length, 12);
});

test("the bytes may sit anywhere in a larger buffer", () => {
    const bytes = quad();
    const padded = new Uint8Array(bytes.length + 7);
    padded.set(bytes, 7);
    assert.deepEqual([...readGlbMesh(padded.subarray(7)).positions], POSITIONS);
});

test("what cannot be read is refused with the reason", () => {
    assert.throws(() => readGlbMesh(new Uint8Array(64)), /not a binary glTF/);
    const doc = {asset: {version: "2.0"}, meshes: [{primitives: [{attributes: {POSITION: 0}, extensions: {KHR_draco_mesh_compression: {}}}]}]};
    assert.throws(() => readGlbMesh(glb(doc, new Uint8Array(4))), /compressed \(KHR_draco_mesh_compression\)/);
    assert.throws(() => readGlbMesh(glb({asset: {version: "2.0"}}, new Uint8Array(4))), /no mesh/);
    assert.throws(() => readGlbMesh(quad({mode: 5})), /primitive mode \(5\)/);
});
