// The result layer on the three.js-free viewport: a step's colours and displacement, and the
// deformation scale, go to the renderer's geometry and instance through the calls its interface
// names. The same calls as on the three.js viewport (utils/threeResultLayer.test.ts).
import assert from "node:assert/strict";
import {test} from "node:test";

import {InstanceFlag, Topology, type AdacppModule, type WebRendererApi} from "@/viewport/adacpp/types";
import type {ResultMeshData} from "@/viewport/api/resultLayer";
import {NativeResultLayer} from "@/viewport/native/nativeResultLayer";

const TRIANGLE: ResultMeshData = {
    positions: new Float32Array([0, 0, 0, 2, 0, 0, 0, 3, 0]),
    indices: new Uint32Array([0, 1, 2]),
    topology: "triangles",
};
const RGB = new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 0.5]);
const MOVE = new Float32Array([0, 0, 1, 0, 0, 2, 0, 0, 3]);

/** A renderer that records its calls, over a heap like the wasm module's. */
function fakeRenderer() {
    const heap = new ArrayBuffer(1 << 16);
    let top = 8;
    const freed: number[] = [];
    const module = {
        HEAPU8: new Uint8Array(heap),
        HEAPU32: new Uint32Array(heap),
        HEAPF32: new Float32Array(heap),
        _malloc: (bytes: number) => {
            const p = top;
            top += Math.ceil(bytes / 8) * 8;
            return p;
        },
        _free: (p: number) => void freed.push(p),
    } as unknown as AdacppModule;
    const calls: string[] = [];
    const state = {
        geometry: null as null | {topology: number; positions: number[]; colors: number[]; morph: number[]; indices: number[]; normals: number},
        colors: [] as number[],
        morph: [] as number[],
        instance: null as null | {matrix: number[]; rgba: number[]; flags: number; influence: number},
    };
    const f32 = (p: number, n: number) => [...module.HEAPF32.subarray(p >> 2, (p >> 2) + n)];
    const u32 = (p: number, n: number) => [...module.HEAPU32.subarray(p >> 2, (p >> 2) + n)];
    const renderer = {
        addGeometry: (topology: number, positions: number, n: number, normals: number, colors: number, morph: number, indices: number, indexCount: number) => {
            calls.push("addGeometry");
            state.geometry = {topology, positions: f32(positions, n * 3), colors: u32(colors, n), morph: f32(morph, n * 3), indices: u32(indices, indexCount), normals};
            return 11;
        },
        addInstance: (geometry: number) => {
            calls.push(`addInstance(${geometry})`);
            return 22;
        },
        setInstance: (instance: number, matrix: number, r: number, g: number, b: number, a: number, flags: number, influence: number) => {
            calls.push(`setInstance(${instance})`);
            state.instance = {matrix: f32(matrix, 16), rgba: [r, g, b, a], flags, influence};
        },
        updateColors: (geometry: number, colors: number, n: number) => {
            calls.push(`updateColors(${geometry})`);
            state.colors = u32(colors, n);
        },
        updateMorph: (geometry: number, morph: number, n: number) => {
            calls.push(`updateMorph(${geometry})`);
            state.morph = f32(morph, n * 3);
        },
        removeInstance: (instance: number) => void calls.push(`removeInstance(${instance})`),
        removeGeometry: (geometry: number) => void calls.push(`removeGeometry(${geometry})`),
    } as unknown as WebRendererApi;
    return {module, renderer, calls, state, freed};
}

test("the mesh is one geometry with colours and a morph, and one instance", () => {
    const {module, renderer, state} = fakeRenderer();
    let frames = 0;
    const layer = new NativeResultLayer(module, renderer, TRIANGLE, () => frames++);
    assert.equal(layer.vertexCount, 3);
    assert.equal(state.geometry?.topology, Topology.Triangles);
    assert.deepEqual(state.geometry?.positions, [...TRIANGLE.positions]);
    assert.deepEqual(state.geometry?.indices, [0, 1, 2]);
    assert.deepEqual(state.geometry?.morph, new Array(9).fill(0), "undeformed until a step is set");
    assert.equal(state.geometry?.normals, 0, "the renderer shades by the deformed faces");
    const flags = state.instance!.flags;
    assert.ok(flags & InstanceFlag.MorphRelative, "drawn at base + influence * displacement");
    assert.ok(flags & InstanceFlag.DoubleSided);
    assert.ok(flags & InstanceFlag.Clipped, "cut by the section planes");
    assert.equal(flags & InstanceFlag.VertexColors, 0, "plain until it is painted");
    assert.equal(state.instance!.influence, 0);
    assert.ok(frames > 0, "a frame is asked for");
});

test("colours are packed as RGBA8, the displacement goes up as it is, the scale is the instance's influence", () => {
    const {module, renderer, state, calls} = fakeRenderer();
    const layer = new NativeResultLayer(module, renderer, TRIANGLE, () => {});
    layer.setColors(RGB);
    assert.deepEqual(state.colors, [0xff0000ff, 0xff00ff00, 0xff800000]);
    assert.ok(state.instance!.flags & InstanceFlag.VertexColors);
    layer.setDisplacement(MOVE);
    assert.deepEqual(state.morph, [...MOVE]);
    layer.setInfluence(-0.75);
    assert.equal(state.instance!.influence, -0.75);

    // One frame of playback is one instance update: no vertex data moves.
    calls.length = 0;
    layer.setInfluence(0.5);
    assert.deepEqual(calls, ["setInstance(22)"]);
    calls.length = 0;
    layer.setInfluence(0.5);
    assert.deepEqual(calls, [], "the same scale again is nothing to draw");

    layer.setColorsVisible(false);
    assert.equal(state.instance!.flags & InstanceFlag.VertexColors, 0);
    layer.setVisible(false);
    assert.ok(state.instance!.flags & InstanceFlag.Hidden);
});

test("a line layer keeps its colour, stands where its matrix says, and is not picked", () => {
    const {module, renderer, state} = fakeRenderer();
    const matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 6, 7, 1];
    new NativeResultLayer(module, renderer, {positions: TRIANGLE.positions, indices: new Uint32Array([0, 1, 1, 2]), topology: "lines", color: [1, 1, 1], matrix}, () => {});
    assert.equal(state.geometry?.topology, Topology.Lines);
    assert.deepEqual(state.instance!.rgba, [1, 1, 1, 1]);
    assert.deepEqual(state.instance!.matrix, matrix);
    assert.ok(state.instance!.flags & InstanceFlag.NoPick);
    assert.ok(state.instance!.flags & InstanceFlag.Unlit);
});

test("a plain colour given to the layer shows while its painted colours do not, and its own comes back", () => {
    const {module, renderer, state, calls} = fakeRenderer();
    const layer = new NativeResultLayer(module, renderer, {...TRIANGLE, color: [0.5, 0.5, 0.5]}, () => {});
    layer.setColor([1, 0, 0]);
    assert.deepEqual(state.instance!.rgba, [1, 0, 0, 1]);
    calls.length = 0;
    layer.setColor([1, 0, 0]);
    assert.deepEqual(calls, [], "the same colour again is nothing to draw");

    // Painted colours are drawn times the layer's own colour, so that one is back under them.
    layer.setColors(RGB);
    assert.deepEqual(state.instance!.rgba, [0.5, 0.5, 0.5, 1]);
    layer.setColorsVisible(false);
    assert.deepEqual(state.instance!.rgba, [1, 0, 0, 1]);

    layer.setColor(null);
    assert.deepEqual(state.instance!.rgba, [0.5, 0.5, 0.5, 1]);
});

test("a removed layer, or one whose renderer was cleared, does nothing more", () => {
    const a = fakeRenderer();
    const removed = new NativeResultLayer(a.module, a.renderer, TRIANGLE, () => {});
    removed.remove();
    assert.deepEqual(a.calls.slice(-2), ["removeInstance(22)", "removeGeometry(11)"]);
    assert.equal(a.freed.length, 5, "its heap buffers are given back");

    const b = fakeRenderer();
    const cleared = new NativeResultLayer(b.module, b.renderer, TRIANGLE, () => {});
    cleared.detach();
    b.calls.length = 0;
    cleared.setColors(RGB);
    cleared.setDisplacement(MOVE);
    cleared.setInfluence(1);
    cleared.remove();
    assert.deepEqual(b.calls, [], "the renderer's ids are gone with what it drew");
});
