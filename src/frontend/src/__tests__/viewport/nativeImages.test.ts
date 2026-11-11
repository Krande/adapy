// Images lying in the world on the three.js-free viewport, as its renderer can draw them today: a
// mesh of one square per pixel, coloured through the vertex colours, up to a size.
import assert from "node:assert/strict";
import {test} from "node:test";

import {InstanceFlag, type AdacppModule, type WebRendererApi} from "@/viewport/adacpp/types";
import type {ResultMeshData} from "@/viewport/api/resultLayer";
import {createNativeImageLayer, MAX_CELLS} from "@/viewport/native/nativeImages";
import {NativeResultLayer} from "@/viewport/native/nativeResultLayer";
import type {NativeScene} from "@/viewport/native/nativeScene";

interface Geometry {
    id: number;
    positions: number[];
    indices: number[];
    colors: number[];
    morph: number[];
    removed: boolean;
    colorWrites: number;
    morphWrites: number;
}

/** A scene whose result layers are the real ones, over a renderer that records what it is given. */
function fakeScene(heapBytes = 1 << 20) {
    const heap = new ArrayBuffer(heapBytes);
    let top = 8;
    const module = {
        HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap),
        _malloc: (bytes: number) => ((top += Math.ceil(bytes / 8) * 8), top - Math.ceil(bytes / 8) * 8),
        _free: () => {},
    } as unknown as AdacppModule;
    const geometries: Geometry[] = [];
    const instance = {flags: 0, alpha: 1, influence: 0, matrix: [] as number[]};
    const f32 = (p: number, n: number) => [...module.HEAPF32.subarray(p >> 2, (p >> 2) + n)];
    const u32 = (p: number, n: number) => [...module.HEAPU32.subarray(p >> 2, (p >> 2) + n)];
    const renderer = {
        addGeometry: (_topology: number, positions: number, n: number, _normals: number, colors: number, morph: number, indices: number, indexCount: number) => {
            geometries.push({
                id: geometries.length + 1, positions: f32(positions, n * 3), indices: u32(indices, indexCount), colors: u32(colors, n), morph: f32(morph, n * 3),
                removed: false, colorWrites: 0, morphWrites: 0,
            });
            return geometries.length;
        },
        addInstance: (geometry: number) => geometry * 100,
        setInstance: (_i: number, matrix: number, _r: number, _g: number, _b: number, a: number, flags: number, influence: number) => {
            Object.assign(instance, {flags, alpha: a, influence, matrix: f32(matrix, 16)});
        },
        updateColors: (geometry: number, colors: number, n: number) => {
            geometries[geometry - 1].colors = u32(colors, n);
            geometries[geometry - 1].colorWrites++;
        },
        updateMorph: (geometry: number, morph: number, n: number) => {
            geometries[geometry - 1].morph = f32(morph, n * 3);
            geometries[geometry - 1].morphWrites++;
        },
        removeInstance: () => {},
        removeGeometry: (geometry: number) => void (geometries[geometry - 1].removed = true),
    } as unknown as WebRendererApi;
    const scene = {
        sceneEpoch: 0,
        onLoaded: new Set<() => void>(),
        createResultLayer: (data: ResultMeshData) => new NativeResultLayer(module, renderer, data, () => {}),
    };
    return {scene, geometries, instance, layer: createNativeImageLayer(scene as unknown as NativeScene)};
}

const RED = [255, 0, 0, 255], GREEN = [0, 255, 0, 255], BLUE = [0, 0, 255, 255], CLEAR = [0, 0, 0, 0], GREY = [128, 128, 128, 255];
const pixels = (...px: number[][]): Uint8Array => Uint8Array.from(px.flat());
/** A packed colour (red in the low byte) as [r, g, b]. */
const rgb = (packed: number): number[] => [packed & 255, (packed >> 8) & 255, (packed >> 16) & 255];
/** Where the four corners of pixel `cell` are drawn: the mesh's vertices plus their morph. */
function corners(g: Geometry, cell: number): number[][] {
    return [0, 1, 2, 3].map((k) => [0, 1, 2].map((a) => g.positions[(cell * 4 + k) * 3 + a] + g.morph[(cell * 4 + k) * 3 + a]));
}

test("an image is one flat square per pixel: its first row along u at the origin", () => {
    const {geometries, instance, layer} = fakeScene();
    const data = pixels(RED, GREEN, BLUE, GREY, RED, GREEN);
    layer.add({origin: [10, 20, 0], u: [3, 0, 0], v: [0, 4, 0], width: 3, height: 2, pixels: data});
    data.fill(7); // read during the call, not kept
    const [g] = geometries;
    assert.equal(g.positions.length, 6 * 4 * 3, "four vertices a pixel, so each has its colour to its edge");
    assert.equal(g.indices.length, 6 * 6);
    assert.deepEqual(corners(g, 0), [[10, 20, 0], [11, 20, 0], [11, 22, 0], [10, 22, 0]]);
    assert.deepEqual(corners(g, 5), [[12, 22, 0], [13, 22, 0], [13, 24, 0], [12, 24, 0]], "the last pixel of the second row");
    assert.deepEqual(g.indices.slice(0, 6), [0, 1, 2, 0, 2, 3]);
    // sRGB bytes become the linear bytes the renderer keeps: the ends stay, the middle darkens.
    assert.deepEqual(rgb(g.colors[0]), [255, 0, 0]);
    assert.deepEqual(rgb(g.colors[4 * 3]), [55, 55, 55]);
    assert.deepEqual(g.colors.slice(4, 8).map(rgb), [[0, 255, 0], [0, 255, 0], [0, 255, 0], [0, 255, 0]]);
    assert.ok(instance.flags & InstanceFlag.Unlit && instance.flags & InstanceFlag.NoPick && instance.flags & InstanceFlag.VertexColors);
    assert.equal(instance.influence, 1);
});

test("new pixels of the same size are written in place", () => {
    const {geometries, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [2, 0, 0], v: [0, 1, 0], width: 2, height: 1, pixels: pixels(RED, GREEN)});
    const [g] = geometries;
    const writes = g.colorWrites;
    layer.setPixels(handle, pixels(BLUE, RED));
    assert.equal(geometries.length, 1, "no mesh is made for new pixels");
    assert.equal(g.colorWrites, writes + 1);
    assert.deepEqual(rgb(g.colors[0]), [0, 0, 255]);
    assert.equal(g.morphWrites, 0, "every pixel is still drawn: the mesh's shape is not written");
});

test("a pixel that is clear is left out, and comes back when it is given a colour", () => {
    const {geometries, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [2, 0, 0], v: [0, 1, 0], width: 2, height: 1, pixels: pixels(RED, CLEAR)});
    const [g] = geometries;
    assert.equal(g.morphWrites, 1);
    assert.deepEqual(corners(g, 1), [[1, 0, 0], [1, 0, 0], [1, 0, 0], [1, 0, 0]], "folded onto one corner: no area");
    assert.deepEqual(corners(g, 0), [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]);
    // The same pixels drawn: only colours are written.
    layer.setPixels(handle, pixels(GREEN, CLEAR));
    assert.equal(g.morphWrites, 1);
    layer.setPixels(handle, pixels(CLEAR, BLUE));
    assert.equal(g.morphWrites, 2);
    assert.deepEqual(corners(g, 1), [[1, 0, 0], [2, 0, 0], [2, 1, 0], [1, 1, 0]]);
    assert.deepEqual(corners(g, 0), [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]]);
});

test("another size, or another place, is another mesh, and the old one is removed", () => {
    const {geometries, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [2, 0, 0], v: [0, 1, 0], width: 2, height: 1, pixels: pixels(RED, GREEN)});
    layer.setPixels(handle, pixels(RED, GREEN, BLUE, GREY), 2, 2);
    assert.equal(geometries.length, 2);
    assert.equal(geometries[0].removed, true);
    assert.equal(geometries[1].positions.length, 4 * 4 * 3);
    layer.setRect(handle, {origin: [5, 0, 0], u: [2, 0, 0], v: [0, 2, 0]});
    assert.equal(geometries.length, 3);
    assert.equal(geometries[1].removed, true);
    assert.deepEqual(corners(geometries[2], 3)[2], [7, 2, 0]);
    assert.deepEqual(rgb(geometries[2].colors[4]), [0, 255, 0], "with the pixels it had");
    layer.remove(handle);
    assert.equal(geometries[2].removed, true);
});

test("opacity, visibility and the matrix change in place", () => {
    const {geometries, instance, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: pixels(RED), opacity: 0.5, onTop: true});
    assert.equal(instance.alpha, 0.5);
    assert.ok(instance.flags & InstanceFlag.OnTop && instance.flags & InstanceFlag.Transparent);
    layer.setLook(handle, {visible: false});
    assert.ok(instance.flags & InstanceFlag.Hidden);
    layer.setLook(handle, {visible: true, opacity: 1, matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 7, 8, 9, 1]});
    assert.equal(instance.flags & InstanceFlag.Hidden, 0);
    assert.equal(instance.alpha, 1);
    assert.deepEqual(instance.matrix.slice(12, 15), [7, 8, 9]);
    assert.equal(geometries.length, 1);
});

test("an image above the limit is not drawn and the layer says why; a smaller one given later is", () => {
    const {geometries, layer} = fakeScene();
    const warn = console.warn;
    const warned: string[] = [];
    console.warn = (text: string) => void warned.push(text);
    try {
        const side = Math.ceil(Math.sqrt(MAX_CELLS)) + 1;
        assert.equal(layer.unavailable(16, 16), null);
        assert.match(layer.unavailable(side, side) ?? "", /one square per pixel/);
        const handle = layer.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: side, height: side, pixels: new Uint8Array(side * side * 4)});
        assert.equal(geometries.length, 0);
        assert.equal(warned.length, 1);
        layer.setPixels(handle, pixels(RED), 1, 1);
        assert.equal(geometries.length, 1);
    } finally {
        console.warn = warn;
    }
});

test("an image is made again after a load emptied the scene, as it was last", () => {
    const {scene, geometries, instance, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: pixels(RED)});
    layer.setPixels(handle, pixels(BLUE));
    layer.setLook(handle, {opacity: 0.25});
    for (const listener of scene.onLoaded) listener();
    assert.equal(geometries.length, 1, "a load that emptied nothing makes nothing");
    scene.sceneEpoch++;
    for (const listener of scene.onLoaded) listener();
    assert.equal(geometries.length, 2);
    assert.deepEqual(rgb(geometries[1].colors[0]), [0, 0, 255]);
    assert.equal(instance.alpha, 0.25);
    layer.dispose();
    assert.equal(scene.onLoaded.size, 0);
});
