// Which objects of a GLB have no colour of their own, read from its JSON alone: what the
// three.js-free viewport draws in the default colour.
import assert from "node:assert/strict";
import {test} from "node:test";

import {glbUncoloured, UNCOLOURED_BLACK, UNCOLOURED_GREY} from "@/utils/glb/glbUncoloured";

/** A GLB that is only its JSON chunk. */
function glb(doc: unknown): Uint8Array {
    let text = JSON.stringify(doc);
    while (text.length % 4) text += " ";
    const json = new TextEncoder().encode(text);
    const bytes = new Uint8Array(20 + json.length);
    const view = new DataView(bytes.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, bytes.length, true);
    view.setUint32(12, json.length, true);
    view.setUint32(16, 0x4e4f534a, true);
    bytes.set(json, 20);
    return bytes;
}

const marked = (bytes: Uint8Array, nodeObjects = false): Record<string, number> => {
    const out: Record<string, number> = {};
    glbUncoloured(bytes, nodeObjects, (id, kind) => void (out[id] = kind));
    return out;
};

const MATERIALS = [
    {pbrMetallicRoughness: {baseColorFactor: [0.8, 0.8, 0.8, 1]}}, // 0: the grey ada writes
    {pbrMetallicRoughness: {baseColorFactor: [0, 0, 0, 1]}}, // 1: black
    {pbrMetallicRoughness: {baseColorFactor: [1, 0, 0, 1]}}, // 2: a colour
    {pbrMetallicRoughness: {baseColorFactor: [0, 0, 0, 1], baseColorTexture: {index: 0}}}, // 3: textured
    {pbrMetallicRoughness: {}}, // 4: glTF's default, white
];

test("ada's draw ranges: every object of a node drawn in grey or black has no colour", () => {
    const bytes = glb({
        scenes: [{extras: {id_hierarchy: {}, draw_ranges_node0: {a: [0, 3], b: [3, 3]}, draw_ranges_node1: {c: [0, 3]}, draw_ranges_node2: {d: [0, 3]}}}],
        nodes: [{mesh: 0}, {mesh: 1}, {mesh: 2}],
        meshes: [{primitives: [{material: 0}]}, {primitives: [{material: 1}]}, {primitives: [{material: 2}]}],
        materials: MATERIALS,
    });
    assert.deepEqual(marked(bytes), {a: UNCOLOURED_GREY, b: UNCOLOURED_GREY, c: UNCOLOURED_BLACK});
    const found = glbUncoloured(bytes, false, () => {});
    assert.equal(found?.count, 3);
    assert.ok(found?.extras && "id_hierarchy" in found.extras, "the scene's extras come back parsed");
});

test("a GLB whose objects are its nodes: a node is uncoloured when everything it draws is", () => {
    const bytes = glb({
        nodes: [{mesh: 0}, {mesh: 1}, {mesh: 2}, {mesh: 3}, {mesh: 4}, {}],
        meshes: [
            {primitives: [{material: 0}, {material: 1}]}, // grey and black: no colour
            {primitives: [{material: 0}, {material: 2}]}, // one of them is red
            {primitives: [{material: 3}]}, // textured
            {primitives: [{material: 0, attributes: {POSITION: 0, COLOR_0: 1}}]}, // coloured by vertex
            {primitives: [{material: 4}, {}]}, // white, and no material at all
        ],
        materials: MATERIALS,
    });
    assert.deepEqual(marked(bytes, true), {"0": UNCOLOURED_BLACK});
});

test("what is not a GLB, or has no materials, marks nothing", () => {
    assert.equal(glbUncoloured(new Uint8Array(4), false, () => assert.fail()), null);
    assert.equal(glbUncoloured(new Uint8Array(64), false, () => assert.fail()), null);
    assert.deepEqual(marked(glb({scenes: [{extras: {draw_ranges_node0: {a: [0, 3]}}}], nodes: [{mesh: 0}], meshes: [{primitives: [{}]}]})), {});
});

test("a model of 100,000 objects is read in a fraction of a second", () => {
    const ranges: Record<string, [number, number]> = {};
    for (let i = 0; i < 100_000; i++) ranges[`object-${i}`] = [i * 36, 36];
    const bytes = glb({
        scenes: [{extras: {draw_ranges_node0: ranges}}],
        nodes: [{mesh: 0}],
        meshes: [{primitives: [{material: 0}]}],
        materials: MATERIALS,
    });
    let n = 0;
    const t0 = performance.now();
    glbUncoloured(bytes, false, () => void n++);
    const ms = performance.now() - t0;
    console.log(`glbUncoloured: ${(bytes.length / 1e6).toFixed(1)} MB of JSON, 100k objects, ${ms.toFixed(1)} ms`);
    assert.equal(n, 100_000);
    assert.ok(ms < 1000, `${ms.toFixed(0)} ms`);
});
