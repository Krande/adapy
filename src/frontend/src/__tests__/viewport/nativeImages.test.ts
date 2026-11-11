// Images lying in the world on the three.js-free viewport: a textured quad of the renderer each,
// placed so that the image's first row lies at its origin, its pixels a texture written in place.
import assert from "node:assert/strict";
import {test} from "node:test";

import {InstanceFlag} from "@/viewport/adacpp/types";
import {createNativeImageLayer, quadMatrix} from "@/viewport/native/nativeImages";
import type {NativeScene} from "@/viewport/native/nativeScene";

interface Quad {
    texture: number;
    matrix: number[];
    opacity: number;
    flags: number;
    removed: boolean;
}

interface Texture {
    pixels: number[];
    width: number;
    height: number;
    writes: number;
    removed: boolean;
}

/** A scene that records the quads and textures it is asked for. */
function fakeScene(drawsQuads = true) {
    const quads: Quad[] = [];
    const textures: Texture[] = [];
    const scene = {
        sceneEpoch: 0,
        onLoaded: new Set<() => void>(),
        drawsQuads,
        addPlainTexture: (pixels: Uint8Array, width: number, height: number) => textures.push({pixels: [...pixels], width, height, writes: 0, removed: false}),
        updateTexture: (texture: number, pixels: Uint8Array, width: number, height: number) => {
            const t = textures[texture - 1];
            Object.assign(t, {pixels: [...pixels], width, height, writes: t.writes + 1});
        },
        removeTexture: (texture: number) => void (textures[texture - 1].removed = true),
        addQuad: () => quads.push({texture: 0, matrix: [], opacity: 1, flags: 0, removed: false}),
        setQuad: (quad: number, texture: number, matrix: ArrayLike<number>, opacity: number, flags: number) => {
            Object.assign(quads[quad - 1], {texture, matrix: Array.from(matrix), opacity, flags});
        },
        removeQuad: (quad: number) => void (quads[quad - 1].removed = true),
    };
    return {scene, quads, textures, layer: createNativeImageLayer(scene as unknown as NativeScene)};
}

/** Where a point of the renderer's unit square (x, y in -0.5..0.5) is in the world. */
function at(m: ArrayLike<number>, x: number, y: number): number[] {
    return [0, 1, 2].map((r) => Math.round((m[r] * x + m[4 + r] * y + m[12 + r]) * 1e6) / 1e6);
}

const RED = [255, 0, 0, 255], GREEN = [0, 255, 0, 128], BLUE = [0, 0, 255, 0];
const pixels = (...px: number[][]): Uint8Array => Uint8Array.from(px.flat());

test("the quad is placed so the texture's first row lies along u at the origin", () => {
    const m = quadMatrix({origin: [10, 20, 1], u: [4, 0, 0], v: [0, 2, 0]}, null, new Array<number>(16));
    // The renderer has a texture's first row on the square's upper edge, its first column on the left.
    assert.deepEqual(at(m, -0.5, 0.5), [10, 20, 1], "first row, first column: the origin");
    assert.deepEqual(at(m, 0.5, 0.5), [14, 20, 1], "along the first row: along u");
    assert.deepEqual(at(m, -0.5, -0.5), [10, 22, 1], "the last row: at the end of v");
    assert.deepEqual(at(m, 0, 0), [12, 21, 1]);
    // Where the image's coordinates stand in the world comes on top.
    const moved = quadMatrix({origin: [10, 20, 1], u: [4, 0, 0], v: [0, 2, 0]}, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 100, 200, 300, 1], new Array<number>(16));
    assert.deepEqual(at(moved, -0.5, 0.5), [110, 220, 301]);
});

test("an image is one quad and one texture of its pixels, as they were given", () => {
    const {quads, textures, layer} = fakeScene();
    const data = pixels(RED, GREEN, BLUE, RED);
    layer.add({origin: [0, 0, 0], u: [2, 0, 0], v: [0, 2, 0], width: 2, height: 2, pixels: data, nearest: true, clipped: true, opacity: 0.5});
    data.fill(7); // read during the call, not kept
    assert.equal(textures.length, 1);
    assert.deepEqual(textures[0].pixels, [...RED, ...GREEN, ...BLUE, ...RED], "no row is moved and each pixel keeps its alpha");
    assert.deepEqual([textures[0].width, textures[0].height], [2, 2]);
    const [quad] = quads;
    assert.equal(quad.texture, 1);
    assert.equal(quad.opacity, 0.5);
    assert.ok(quad.flags & InstanceFlag.Nearest && quad.flags & InstanceFlag.Clipped && quad.flags & InstanceFlag.DepthOffset);
    assert.equal(quad.flags & (InstanceFlag.OnTop | InstanceFlag.Hidden), 0);
    assert.deepEqual(at(quad.matrix, -0.5, 0.5), [0, 0, 0]);
    assert.equal(layer.unavailable(8192, 8192), null, "no limit of its own");
});

test("new pixels go into the texture that is there, at the same size or another", () => {
    const {quads, textures, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 2, height: 1, pixels: pixels(RED, GREEN)});
    layer.setPixels(handle, pixels(BLUE, RED));
    assert.deepEqual([textures.length, textures[0].writes], [1, 1]);
    assert.deepEqual(textures[0].pixels, [...BLUE, ...RED]);
    layer.setPixels(handle, pixels(RED, GREEN, BLUE, RED, GREEN, BLUE), 3, 2);
    assert.deepEqual([textures.length, textures[0].writes, textures[0].width, textures[0].height], [1, 2, 3, 2], "the texture keeps its id");
    assert.equal(quads.length, 1, "and the quad is as it was");
    layer.setPixels(handle, pixels(RED), 3, 2);
    assert.equal(textures[0].writes, 2, "too few pixels for the size are not an image");
});

test("place, opacity and visibility change the quad that is there", () => {
    const {quads, textures, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: pixels(RED), onTop: true});
    const [quad] = quads;
    assert.ok(quad.flags & InstanceFlag.OnTop);
    assert.equal(quad.flags & (InstanceFlag.DepthOffset | InstanceFlag.Nearest), 0);
    layer.setLook(handle, {visible: false, opacity: 0.25});
    assert.ok(quad.flags & InstanceFlag.Hidden);
    assert.equal(quad.opacity, 0.25);
    layer.setLook(handle, {visible: true, matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 7, 8, 9, 1]});
    assert.equal(quad.flags & InstanceFlag.Hidden, 0);
    assert.deepEqual(at(quad.matrix, -0.5, 0.5), [7, 8, 9]);
    layer.setRect(handle, {origin: [1, 1, 1], u: [0, 2, 0], v: [0, 0, 2]});
    assert.deepEqual(at(quad.matrix, 0.5, -0.5), [8, 11, 12]);
    assert.deepEqual([quads.length, textures.length, textures[0].writes], [1, 1, 0]);
    layer.remove(handle);
    assert.deepEqual([quad.removed, textures[0].removed], [true, true]);
});

test("an image is made again after a load emptied the scene, as it was last", () => {
    const {scene, quads, textures, layer} = fakeScene();
    const handle = layer.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: pixels(RED)});
    layer.setPixels(handle, pixels(GREEN));
    layer.setLook(handle, {opacity: 0.25});
    for (const listener of scene.onLoaded) listener();
    assert.equal(quads.length, 1, "a load that emptied nothing makes nothing");
    scene.sceneEpoch++;
    for (const listener of scene.onLoaded) listener();
    assert.deepEqual([quads.length, textures.length], [2, 2]);
    assert.deepEqual(textures[1].pixels, GREEN);
    assert.equal(quads[1].opacity, 0.25);
    assert.deepEqual([quads[0].removed, textures[0].removed], [false, false], "what the renderer dropped is not removed again");
    layer.dispose();
    assert.deepEqual([quads[1].removed, scene.onLoaded.size], [true, 0]);
});

test("a renderer module without quads draws nothing and the layer says why", () => {
    const {quads, textures, layer} = fakeScene(false);
    const handle = layer.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: pixels(RED)});
    layer.setPixels(handle, pixels(GREEN));
    layer.setLook(handle, {opacity: 0.5});
    assert.deepEqual([quads.length, textures.length], [0, 0]);
    assert.match(layer.unavailable(1, 1) ?? "", /older than this viewer/);
});
